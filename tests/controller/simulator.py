"""
Python port of the ESPHome irrigation controller logic.

Mirrors firmware/packages/{02-zones,05-engine,06-schedule}.yaml behavior
closely enough for end-to-end scenario testing with mocked sensors.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Generator, Iterator, Optional

from tests.controller.config import ControllerConfig, FALLBACK_START_EPOCH
from tests.controller.mock_sensors import MockFlow, MockPressure, MockTime


@dataclass(frozen=True)
class BackgroundActivity:
    """Counters from the simulator tick loop (proxy for ESP32 main-loop liveness)."""

    ticks: int
    safety_ticks: int
    eval_ticks: int
    plan_ticks: int
    ticks_while_zone_on: int


class EventType(enum.Enum):
    PREFLIGHT_FAIL = "preflight_fail"
    PREFLIGHT_SKIP = "preflight_skip"
    SCHEDULE_FIRE = "schedule_fire"
    ZONE_ON = "zone_on"
    ZONE_OFF = "zone_off"
    RUN_COMPLETE = "run_complete"
    RUN_CANCEL = "run_cancel"
    PLAN_UPDATED = "plan_updated"
    ALARM = "alarm"


@dataclass
class SimEvent:
    at_epoch: int
    kind: EventType
    detail: str = ""
    zone_id: int = 0


@dataclass
class ZoneRuntimeState:
    last_finished_epoch: int = FALLBACK_START_EPOCH
    scheduled_next_epoch: int = 0
    actual_state: bool = False
    cycle_delivered_gallons: float = 0.0


@dataclass
class RunState:
    zone_id: int = 0
    total_min: float = 0.0
    cycle_min: float = 0.0
    soak_min: float = 0.0
    minutes_done: float = 0.0
    goal_gallons: float = 0.0
    cycle_gallons: float = 0.0
    gallons_done: float = 0.0
    schedule_mode: int = 0
    cancel_requested: bool = False
    cancel_cause: str = ""
    phase_seconds_left: int = 0
    started_pulses: int = 0
    started_ms: int = 0


Tick = None  # generator yield marker


class ControllerSimulator:
    """Discrete-time simulator ticked once per simulated second."""

    PLAN_INTERVAL = 30
    EVAL_INTERVAL = 60

    def __init__(
        self,
        config: ControllerConfig,
        clock: MockTime,
        pressure: MockPressure,
        flow: MockFlow,
    ):
        self.config = config
        self.clock = clock
        self.pressure = pressure
        self.flow = flow

        self.zones: list[ZoneRuntimeState] = [ZoneRuntimeState() for _ in range(8)]
        self.run = RunState()

        self.currently_on_zone: int = 0
        self.zone_started_at_ms: int = 0
        self.current_phase: str = "idle"
        self.phase_started_ms: int = 0
        self.phase_total_ms: int = 0

        self.pre_flight_passed: bool = False
        self.pre_flight_reason: str = ""
        self.any_alarm_latched: bool = False
        self.alarms: set[str] = set()

        self.skip_next_run_pending: bool = False
        self.due_zone_id: int = 0
        self.last_non_completed_attempt_epoch: int = 0
        self.schedule_gate_reason: str = "none"
        self.schedule_gate_due_zone: int = 0
        self.last_run_started_epoch: int = 0
        self.last_run_finished_epoch: int = 0
        self.last_run_outcome: str = ""

        self.rain_sensor_last_wet_epoch: int = 0
        self.rain_forecast_last_high_epoch: int = 0
        self.stamp_cadence_on_zone_off: bool = True

        self._script: Optional[Iterator] = None
        self._script_name: str = ""
        self._tick_count: int = 0
        self._safety_tick_count: int = 0
        self._eval_tick_count: int = 0
        self._plan_tick_count: int = 0
        self._ticks_while_zone_on: int = 0
        self._events: list[SimEvent] = []

        # Mid-run cancel debounce state (1s safety interval).
        self._high_flow_first_ms: int = 0
        self._low_psi_first_ms: int = 0
        self._high_psi_first_ms: int = 0
        self._phantom_first_ms: int = 0
        self._no_flow_first_ms: int = 0

    # ------------------------------------------------------------------ API
    @property
    def events(self) -> list[SimEvent]:
        return list(self._events)

    def clear_events(self) -> None:
        self._events.clear()

    def zone_last_finished(self, zone_id: int) -> int:
        return self.zones[zone_id - 1].last_finished_epoch

    def zone_scheduled_next(self, zone_id: int) -> int:
        return self.zones[zone_id - 1].scheduled_next_epoch

    def next_due_zone(self) -> int:
        now = self.clock.epoch
        for zid in range(1, 9):
            if not self.config.zone_enabled(zid):
                continue
            z = self.config.zone(zid)
            last = self.zones[zid - 1].last_finished_epoch or self.config.fallback_start_epoch
            interval_s = int(z.min_interval_hours * 3600)
            if now >= last + interval_s:
                return zid
        return 0

    def is_script_running(self) -> bool:
        return self._script is not None

    def background_activity(self) -> BackgroundActivity:
        return BackgroundActivity(
            ticks=self._tick_count,
            safety_ticks=self._safety_tick_count,
            eval_ticks=self._eval_tick_count,
            plan_ticks=self._plan_tick_count,
            ticks_while_zone_on=self._ticks_while_zone_on,
        )

    def tick(self) -> None:
        """Advance simulation by one second."""
        self.clock.advance(1)
        self._tick_count += 1
        now_ms = self.clock.boot_ms

        self.flow.tick(self.currently_on_zone != 0, now_ms)
        self._run_safety_1s(now_ms)
        self._safety_tick_count += 1
        self._run_runtime_cap_1s(now_ms)

        if self.currently_on_zone != 0:
            self._ticks_while_zone_on += 1

        if self._tick_count % self.PLAN_INTERVAL == 0:
            self._update_plan_readout()
            self._plan_tick_count += 1
        if self._tick_count % self.EVAL_INTERVAL == 0:
            self._cadence_evaluator()
            self._eval_tick_count += 1

        # Scripts step last so evaluator fires can progress in the same tick.
        self._step_script()

    def advance(self, seconds: int) -> None:
        for _ in range(seconds):
            self.tick()

    def clear_fault(self) -> None:
        self.any_alarm_latched = False
        self.alarms.clear()

    def clear_recoverable_alarms(self) -> None:
        self.alarms -= {
            "preflight",
            "no_flow",
            "high_flow",
            "low_pressure",
            "high_pressure",
            "runtime_exceeded",
        }
        if "phantom_flow" not in self.alarms:
            self.any_alarm_latched = False

    # ---------------------------------------------------------------- events
    def _emit(self, kind: EventType, detail: str = "", zone_id: int = 0) -> None:
        self._events.append(SimEvent(self.clock.epoch, kind, detail, zone_id))

    # ----------------------------------------------------------- script engine
    def _start_script(self, name: str, gen: Generator) -> None:
        if self._script is not None:
            raise RuntimeError(f"Script {self._script_name} already running")
        self._script = gen
        self._script_name = name
        self._step_script()

    def _step_script(self) -> None:
        if self._script is None:
            return
        try:
            next(self._script)
        except StopIteration:
            self._script = None
            self._script_name = ""

    def _delay(self, seconds: int) -> Generator:
        for _ in range(max(0, seconds)):
            yield Tick

    # ------------------------------------------------------------- drive_zone
    def _drive_zone(self, zone_id: int, state: bool, stamp_cadence: Optional[bool] = None) -> None:
        if stamp_cadence is not None:
            self.stamp_cadence_on_zone_off = stamp_cadence
        zs = self.zones[zone_id - 1]
        zs.actual_state = state
        if state:
            self.currently_on_zone = zone_id
            self.zone_started_at_ms = self.clock.boot_ms
            self._high_flow_first_ms = 0
            self._low_psi_first_ms = 0
            self._high_psi_first_ms = 0
            self._no_flow_first_ms = 0
            self._emit(EventType.ZONE_ON, zone_id=zone_id)
        elif self.currently_on_zone == zone_id:
            now_e = self.clock.epoch
            if now_e > 0 and self.stamp_cadence_on_zone_off:
                zs.last_finished_epoch = now_e
            self.stamp_cadence_on_zone_off = True
            self.currently_on_zone = 0
            self.zone_started_at_ms = 0
            self._emit(EventType.ZONE_OFF, zone_id=zone_id)

    # ---------------------------------------------------------- pre-flight
    def _run_pre_flight(self, is_schedule: bool) -> Generator:
        cfg = self.config
        self.pre_flight_passed = True
        self.pre_flight_reason = ""

        if not cfg.master_enable:
            self.pre_flight_passed = False
            self.pre_flight_reason = "master_enable_off"
        if cfg.emergency_stop:
            self.pre_flight_passed = False
            self.pre_flight_reason = "emergency_stop_latched"
        if not cfg.time_synced:
            self.pre_flight_passed = False
            self.pre_flight_reason = "time_not_synced"

        if cfg.gate_rain_sensor:
            if cfg.rain_sensor_wet:
                self.pre_flight_passed = False
                self.pre_flight_reason = "rain_sensor_wet"
            else:
                hold_s = int(cfg.rain_hold_hours_after_sensor * 3600)
                if (
                    self.rain_sensor_last_wet_epoch > 0
                    and self.clock.epoch - self.rain_sensor_last_wet_epoch < hold_s
                ):
                    self.pre_flight_passed = False
                    self.pre_flight_reason = "rain_hold_after_sensor"

        now_epoch = self.clock.epoch
        ttl_s = int(cfg.rain_mm_max_age_hours * 3600)
        effective_mm = cfg.rain_mm_last_48h
        pushed = cfg.rain_mm_last_pushed_epoch
        if pushed == 0 or now_epoch - pushed > ttl_s:
            effective_mm = 0.0
        if effective_mm > cfg.rain_mm_threshold_48h:
            self.pre_flight_passed = False
            self.pre_flight_reason = "rain_forecast_high"
            self.rain_forecast_last_high_epoch = now_epoch
        else:
            hold_s = int(cfg.rain_hold_hours_after_forecast * 3600)
            if (
                self.rain_forecast_last_high_epoch > 0
                and now_epoch - self.rain_forecast_last_high_epoch < hold_s
            ):
                self.pre_flight_passed = False
                self.pre_flight_reason = "rain_forecast_hold"

        if self.currently_on_zone == 0 and cfg.gate_static_pressure_preflight:
            yield from self._delay(1)
            p = self.pressure.read(zone_on=False)
            if p < cfg.pressure_static_min_psi:
                self.pre_flight_passed = False
                self.pre_flight_reason = "pressure_too_low"
            elif p > cfg.pressure_static_max_psi:
                self.pre_flight_passed = False
                self.pre_flight_reason = "pressure_too_high"

        if is_schedule and not cfg.fallback_schedule_enabled:
            self.pre_flight_passed = False
            self.pre_flight_reason = "schedule_disabled"

        if self.any_alarm_latched:
            self.pre_flight_passed = False
            self.pre_flight_reason = "alarm_latched"

        if not self.pre_flight_passed:
            benign = self.pre_flight_reason in {
                "rain_sensor_wet",
                "rain_hold_after_sensor",
                "rain_forecast_high",
                "rain_forecast_hold",
                "pressure_too_low",
                "pressure_too_high",
                "alarm_latched",
            }
            if benign:
                self._emit(EventType.PREFLIGHT_SKIP, self.pre_flight_reason)
            else:
                self._emit(EventType.PREFLIGHT_FAIL, self.pre_flight_reason)
                self._signal_alarm("preflight", blocking=False)
        yield Tick

    # -------------------------------------------------------- run_one_zone (time)
    def _run_one_zone(
        self, zone_id: int, total_min: float, cycle_min: float, soak_min: float
    ) -> Generator:
        zcfg = self.config.zone(zone_id)
        zs = self.zones[zone_id - 1]
        self.stamp_cadence_on_zone_off = True
        self.run = RunState(
            zone_id=zone_id,
            total_min=total_min,
            cycle_min=cycle_min,
            soak_min=soak_min,
            started_pulses=self.flow.pulses_total,
            started_ms=self.clock.boot_ms,
        )

        p = self.pressure.read(zone_on=False)
        if p < zcfg.start_minimum_psi or p > zcfg.start_maximum_psi:
            self.run.cancel_requested = True
            self.run.cancel_cause = "start_pressure_out_of_bounds"

        while self.run.minutes_done < total_min and not self.run.cancel_requested:
            self.current_phase = "running"
            self.phase_started_ms = self.clock.boot_ms
            self.phase_total_ms = int(cycle_min * 60000)
            self._drive_zone(zone_id, True)

            self.run.phase_seconds_left = int(cycle_min * 60)
            while self.run.phase_seconds_left > 0 and not self.run.cancel_requested:
                yield from self._delay(1)
                self.run.phase_seconds_left -= 1
                self.run.minutes_done += 1.0 / 60.0

            if (
                self.run.minutes_done < total_min
                and soak_min > 0
                and not self.run.cancel_requested
            ):
                self.current_phase = "soaking"
                self.phase_started_ms = self.clock.boot_ms
                self.phase_total_ms = int(soak_min * 60000)
                self.stamp_cadence_on_zone_off = False
                self._drive_zone(zone_id, False)
                yield from self._delay(int(soak_min * 60))

        self.stamp_cadence_on_zone_off = not self.run.cancel_requested
        self._drive_zone(zone_id, False)
        if not self.run.cancel_requested and self.clock.epoch > 0:
            zs.last_finished_epoch = self.clock.epoch
        if self.run.cancel_requested:
            self._emit(EventType.RUN_CANCEL, self.run.cancel_cause, zone_id)
        else:
            self._emit(EventType.RUN_COMPLETE, zone_id=zone_id)

    def _set_schedule_gate(self, reason: str, due_zone: int = 0) -> None:
        self.schedule_gate_reason = reason
        self.schedule_gate_due_zone = due_zone

    # --------------------------------------------------- schedule_fire_handler
    def _schedule_fire_handler(self) -> Generator:
        zid = self.due_zone_id
        self._set_schedule_gate("none", 0)
        self.clear_recoverable_alarms()
        yield from self._run_pre_flight(is_schedule=True)
        if not self.pre_flight_passed:
            self.due_zone_id = 0
            return

        if self.config.time_synced:
            self.last_run_started_epoch = self.clock.epoch
        self._emit(EventType.SCHEDULE_FIRE, zone_id=zid)

        zcfg = self.config.zone(zid)
        yield from self._run_one_zone_with_retry(
            zid, zcfg.total_min, zcfg.cycle_min, zcfg.soak_min
        )

        if self.run.cancel_requested and self.config.time_synced:
            self.last_non_completed_attempt_epoch = self.clock.epoch

        self.current_phase = "fault" if self.any_alarm_latched else "idle"
        self.phase_total_ms = 0
        if self.config.time_synced:
            self.last_run_finished_epoch = self.clock.epoch

        if self.run.cancel_requested:
            self.last_run_outcome = f"cancelled_{self.run.cancel_cause}"
        else:
            self.last_run_outcome = "completed"
        self.due_zone_id = 0

    def _run_one_zone_with_retry(
        self, zone_id: int, total_min: float, cycle_min: float, soak_min: float
    ) -> Generator:
        zcfg = self.config.zone(zone_id)
        if zcfg.schedule_mode == 1:
            yield from self._run_one_zone_gallons_target(
                zone_id, zcfg.goal_gallons, zcfg.cycle_gallons, zcfg.soak_min
            )
        else:
            yield from self._run_one_zone(zone_id, total_min, cycle_min, soak_min)

    def _run_one_zone_gallons_target(
        self, zone_id: int, goal: float, cycle_gal: float, soak_min: float
    ) -> Generator:
        zcfg = self.config.zone(zone_id)
        zs = self.zones[zone_id - 1]
        run_base = zs.cycle_delivered_gallons
        self.stamp_cadence_on_zone_off = True
        self.run = RunState(
            zone_id=zone_id,
            soak_min=soak_min,
            goal_gallons=goal,
            cycle_gallons=cycle_gal,
            gallons_done=run_base,
            schedule_mode=1,
            started_pulses=self.flow.pulses_total,
            started_ms=self.clock.boot_ms,
        )

        p = self.pressure.read(zone_on=False)
        if p < zcfg.start_minimum_psi or p > zcfg.start_maximum_psi:
            self.run.cancel_requested = True
            self.run.cancel_cause = "start_pressure_out_of_bounds"

        while self.run.gallons_done < goal and not self.run.cancel_requested:
            self.current_phase = "running"
            chunk_start = self.flow.pulses_total
            self._drive_zone(zone_id, True)

            while not self.run.cancel_requested:
                yield from self._delay(1)
                ppg = self.flow.pulses_per_gallon
                run_pulses = max(0, self.flow.pulses_total - self.run.started_pulses)
                chunk_pulses = max(0, self.flow.pulses_total - chunk_start)
                run_gal = run_pulses / ppg if ppg > 0 else 0.0
                chunk_gal = chunk_pulses / ppg if ppg > 0 else 0.0
                self.run.gallons_done = run_base + run_gal
                zs.cycle_delivered_gallons = self.run.gallons_done
                if self.run.gallons_done >= goal:
                    break
                remaining = goal - self.run.gallons_done
                chunk_limit = min(cycle_gal, remaining) if remaining > 0 else 0.0
                if chunk_gal >= chunk_limit:
                    break

            self.stamp_cadence_on_zone_off = False
            self._drive_zone(zone_id, False)

            if (
                self.run.gallons_done < goal
                and not self.run.cancel_requested
                and soak_min > 0
            ):
                self.current_phase = "soaking"
                yield from self._delay(int(soak_min * 60))

        self.stamp_cadence_on_zone_off = not self.run.cancel_requested
        self._drive_zone(zone_id, False)
        zs.cycle_delivered_gallons = (
            0.0 if self.run.gallons_done >= goal else self.run.gallons_done
        )
        if (
            not self.run.cancel_requested
            and self.run.gallons_done >= goal
            and self.clock.epoch > 0
        ):
            zs.last_finished_epoch = self.clock.epoch
        if self.run.cancel_requested:
            self._emit(EventType.RUN_CANCEL, self.run.cancel_cause, zone_id)
        else:
            self._emit(EventType.RUN_COMPLETE, zone_id=zone_id)

    # -------------------------------------------------------- plan readout 30s
    def _in_watering_window(self) -> bool:
        cfg = self.config
        now_min = self.clock.hour * 60 + self.clock.minute
        start_min = cfg.schedule_start_hour * 60 + cfg.schedule_start_minute
        end_min = cfg.schedule_end_hour * 60 + cfg.schedule_end_minute
        if start_min == end_min:
            return False
        if start_min < end_min:
            return start_min <= now_min < end_min
        return now_min >= start_min or now_min < end_min

    def _is_blackout(self) -> bool:
        mask = self.config.blackout_weekday_bitmask
        return bool((mask >> self.clock.dow_mon0) & 1)

    def _snap_next(self, earliest: int) -> int:
        cfg = self.config
        start_min_cfg = cfg.schedule_start_hour * 60 + cfg.schedule_start_minute
        end_min_cfg = cfg.schedule_end_hour * 60 + cfg.schedule_end_minute
        if start_min_cfg == end_min_cfg:
            return 0

        t = (earliest // 60) * 60
        if t < earliest:
            t += 60

        for _ in range(20160):
            dt = datetime.fromtimestamp(t, tz=self.clock.tz)
            now_min = dt.hour * 60 + dt.minute
            dow_mon0 = dt.weekday()
            if start_min_cfg < end_min_cfg:
                in_window = start_min_cfg <= now_min < end_min_cfg
            else:
                in_window = now_min >= start_min_cfg or now_min < end_min_cfg
            blackout = bool((cfg.blackout_weekday_bitmask >> dow_mon0) & 1)
            if in_window and not blackout:
                return t
            t += 60
        return 0

    def _update_plan_readout(self) -> None:
        cfg = self.config
        if self.currently_on_zone != 0:
            return

        for zs in self.zones:
            zs.scheduled_next_epoch = 0

        if not cfg.fallback_schedule_enabled:
            return
        now = self.clock.epoch
        if now == 0:
            return

        ideals: list[tuple[int, int]] = []  # (ideal_epoch, zone_id)
        for zid in range(1, 9):
            if not cfg.zone_enabled(zid):
                continue
            zcfg = cfg.zone(zid)
            if zcfg.min_interval_hours <= 0:
                continue
            last = self.zones[zid - 1].last_finished_epoch or cfg.fallback_start_epoch
            interval_s = int(zcfg.min_interval_hours * 3600)
            raw_due = last + interval_s
            ideal = self._snap_next(raw_due)
            if ideal:
                ideals.append((ideal, zid))

        ideals.sort(key=lambda x: (x[0], x[1]))

        cursor = 0
        dur_s = max(60, int(cfg.maximum_runtime_minutes * 60))
        assigned: dict[int, int] = {}

        for ideal, zid in ideals:
            if ideal <= now:
                merged = (now // 60) * 60
            else:
                merged = ideal
            if cursor > merged:
                merged = cursor
            actual = self._snap_next(merged)
            if not actual:
                continue
            assigned[zid] = actual
            next_cursor = actual + dur_s
            cursor = self._snap_next(next_cursor) or next_cursor

        for zid in range(1, 9):
            epoch = assigned.get(zid, 0)
            if self.zones[zid - 1].scheduled_next_epoch != epoch:
                self.zones[zid - 1].scheduled_next_epoch = epoch

        self._emit(EventType.PLAN_UPDATED)

    # ----------------------------------------------------- cadence evaluator 60s
    def _cadence_evaluator(self) -> None:
        cfg = self.config
        if not cfg.fallback_schedule_enabled:
            self._set_schedule_gate("schedule_disabled")
            return
        if self.currently_on_zone != 0:
            self._set_schedule_gate("zone_already_running", self.currently_on_zone)
            return
        if self.is_script_running():
            self._set_schedule_gate("script_running")
            return

        now = self.clock.epoch
        if now == 0:
            self._set_schedule_gate("time_not_synced")
            return

        cooldown_s = int(cfg.attempt_cooldown_minutes * 60)
        if (
            cooldown_s > 0
            and self.last_non_completed_attempt_epoch > 0
            and now < self.last_non_completed_attempt_epoch + cooldown_s
        ):
            self._set_schedule_gate("attempt_cooldown")
            return

        picked = self.next_due_zone()
        if picked == 0:
            self._set_schedule_gate("nothing_due")
            return

        if not self._in_watering_window():
            self._set_schedule_gate("outside_watering_window", picked)
            return
        if self._is_blackout():
            self._set_schedule_gate("blackout_day", picked)
            return

        if self.skip_next_run_pending:
            self.skip_next_run_pending = False
            self._set_schedule_gate("skip_next_run", picked)
            return

        self._set_schedule_gate("none", picked)
        self.due_zone_id = picked
        self._start_script("schedule_fire_handler", self._schedule_fire_handler())

    # ----------------------------------------------------------- safety 1s
    def _signal_alarm(self, name: str, blocking: bool = False) -> None:
        self.alarms.add(name)
        self._emit(EventType.ALARM, name)
        if blocking:
            self.any_alarm_latched = True

    def _run_safety_1s(self, now_ms: int) -> None:
        cfg = self.config
        gpm = self.flow.gpm

        if (
            cfg.gate_alarm_phantom_flow
            and self.currently_on_zone == 0
            and gpm > cfg.phantom_flow_gpm
        ):
            if self._phantom_first_ms == 0:
                self._phantom_first_ms = now_ms
            if now_ms - self._phantom_first_ms > 5 * 60 * 1000:
                self._signal_alarm("phantom_flow", blocking=True)
        else:
            self._phantom_first_ms = 0

        if self.run.zone_id == 0 or self.currently_on_zone == 0:
            return

        since_start = now_ms - self.zone_started_at_ms
        zid = self.currently_on_zone
        zcfg = cfg.zone(zid)
        startup_grace_ms = int(cfg.no_flow_grace_s * 1000)

        if cfg.gate_alarm_no_flow and since_start >= startup_grace_ms:
            if gpm < zcfg.min_flow_gpm:
                if self._no_flow_first_ms == 0:
                    self._no_flow_first_ms = now_ms
                if now_ms - self._no_flow_first_ms >= int(cfg.no_flow_sustain_s * 1000):
                    self._signal_alarm("no_flow")
                    self.run.cancel_requested = True
                    self.run.cancel_cause = "no_flow"
                    self._no_flow_first_ms = 0
            else:
                self._no_flow_first_ms = 0
        else:
            self._no_flow_first_ms = 0

        if cfg.gate_alarm_high_flow and gpm > zcfg.max_flow_gpm:
            if self._high_flow_first_ms == 0:
                self._high_flow_first_ms = now_ms
            if now_ms - self._high_flow_first_ms >= cfg.high_flow_grace_s * 1000:
                self._signal_alarm("high_flow")
                self.run.cancel_requested = True
                self.run.cancel_cause = "high_flow"
                self._high_flow_first_ms = 0
        else:
            self._high_flow_first_ms = 0

        p = self.pressure.read(zone_on=True)
        min_running = zcfg.minimum_running_psi
        grace_s = zcfg.minimum_running_psi_grace_seconds
        if cfg.gate_alarm_low_pressure and since_start >= startup_grace_ms:
            if p < min_running:
                if self._low_psi_first_ms == 0:
                    self._low_psi_first_ms = now_ms
                if now_ms - self._low_psi_first_ms >= grace_s * 1000:
                    self._signal_alarm("low_pressure")
                    self.run.cancel_requested = True
                    self.run.cancel_cause = "low_pressure"
                    self._low_psi_first_ms = 0
            else:
                self._low_psi_first_ms = 0

        if cfg.gate_alarm_high_pressure and p > cfg.pressure_high_psi:
            if self._high_psi_first_ms == 0:
                self._high_psi_first_ms = now_ms
            if now_ms - self._high_psi_first_ms >= 10_000:
                self._signal_alarm("high_pressure")
                if cfg.high_pressure_cancels_run:
                    self.run.cancel_requested = True
                    self.run.cancel_cause = "high_pressure"
                self._high_psi_first_ms = 0
        else:
            self._high_psi_first_ms = 0

        if cfg.gate_rain_sensor and cfg.rain_sensor_wet:
            self.rain_sensor_last_wet_epoch = self.clock.epoch
            self.run.cancel_requested = True
            self.run.cancel_cause = "rain"

    def _run_runtime_cap_1s(self, now_ms: int) -> None:
        if self.currently_on_zone == 0:
            return
        elapsed_ms = now_ms - self.zone_started_at_ms
        cap_ms = int(self.config.maximum_runtime_minutes * 60 * 1000)
        if elapsed_ms > cap_ms:
            zid = self.currently_on_zone
            self._drive_zone(zid, False)
            self._signal_alarm("runtime_exceeded")
