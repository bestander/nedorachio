"""
Schedule engine: weekly gallon budget, pre-flight, relay execution.

Canonical Python implementation mirrored by firmware C++.
"""

from __future__ import annotations

from typing import Generator, Iterator, Optional, Protocol

from nedorachio.gates import PreflightContext, evaluate_preflight
from nedorachio.models import (
    BackgroundActivity,
    EventType,
    OperationalConfig,
    RunState,
    SimEvent,
    ZoneRuntimeState,
)
from nedorachio.schedule import (
    accept_ha_weekly_update,
    calendar_week_id,
    effective_rain_mm_this_week,
    effective_weekly_goal,
    in_watering_window,
    is_blackout_day,
    maybe_apply_week_reset,
    pick_next_zone_round_robin,
    update_scheduled_next_epochs,
    weekly_delivered_effective,
)

Tick = None


class Clock(Protocol):
    epoch: int
    boot_ms: int
    tz: object
    hour: int
    minute: int
    dow_mon0: int

    def advance(self, seconds: int) -> None: ...


class PressureSensor(Protocol):
    def read(self, zone_on: bool) -> float: ...


class FlowSensor(Protocol):
    pulses_per_gallon: float
    pulses_total: int
    gpm: float

    def tick(self, zone_on: bool, now_ms: int) -> None: ...


class ControllerSimulator:
    """Discrete-time controller ticked once per simulated second."""

    PLAN_INTERVAL = 30
    EVAL_INTERVAL = 60

    def __init__(
        self,
        config: OperationalConfig,
        clock: Clock,
        pressure: PressureSensor,
        flow: FlowSensor,
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
        self.week_id_shadow: int = 0
        self.last_served_zone_id: int = 0
        self.tracking_source: str = "local"
        self.ha_weekly_last_update_epoch: int = 0
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

        self._high_flow_first_ms: int = 0
        self._low_psi_first_ms: int = 0
        self._high_psi_first_ms: int = 0
        self._phantom_first_ms: int = 0
        self._no_flow_first_ms: int = 0

    @property
    def events(self) -> list[SimEvent]:
        return list(self._events)

    def clear_events(self) -> None:
        self._events.clear()

    def zone_last_finished(self, zone_id: int) -> int:
        return self.zones[zone_id - 1].last_finished_epoch

    def zone_scheduled_next(self, zone_id: int) -> int:
        return self.zones[zone_id - 1].scheduled_next_epoch

    def ha_weekly_feed_valid(self) -> bool:
        if not self.config.time_synced or not self.config.ha_weekly_feed_valid:
            return False
        if self.ha_weekly_last_update_epoch <= 0:
            return False
        age = self.clock.epoch - self.ha_weekly_last_update_epoch
        return age <= self.config.ha_weekly_staleness_seconds

    def on_zone_weekly_delivered(self, zone_id: int, gallons: float) -> None:
        if zone_id < 1 or zone_id > 8:
            return
        zs = self.zones[zone_id - 1]
        ha_val = max(0.0, gallons)
        if not accept_ha_weekly_update(zs.ha_weekly_delivered, ha_val):
            return
        zs.ha_weekly_delivered = ha_val
        zs.weekly_delivered_shadow = max(zs.weekly_delivered_shadow, ha_val)
        self.ha_weekly_last_update_epoch = self.clock.epoch
        self.tracking_source = "ha"
        self.config.tracking_source = "ha"

    def next_pick_zone(self) -> int:
        self._apply_week_reset_if_needed()
        return pick_next_zone_round_robin(
            self.config,
            self.zones,
            self.clock.epoch,
            ha_feed_valid=self.ha_weekly_feed_valid(),
            respect_cooldown=True,
        )

    def next_deficit_zone(self) -> int:
        self._apply_week_reset_if_needed()
        return pick_next_zone_round_robin(
            self.config,
            self.zones,
            self.clock.epoch,
            ha_feed_valid=self.ha_weekly_feed_valid(),
            respect_cooldown=False,
        )

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
        self.clock.advance(1)
        self._tick_count += 1
        now_ms = self.clock.boot_ms

        self.flow.tick(self.currently_on_zone != 0, now_ms)
        self._run_safety_1s(now_ms)
        self._safety_tick_count += 1
        self._run_attempt_cap_1s(now_ms)

        if self.currently_on_zone != 0:
            self._ticks_while_zone_on += 1

        if self._tick_count % self.PLAN_INTERVAL == 0:
            self._update_plan_readout()
            self._plan_tick_count += 1
        if self._tick_count % self.EVAL_INTERVAL == 0:
            self._weekly_budget_evaluator()
            self._eval_tick_count += 1

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

    def _apply_week_reset_if_needed(self) -> None:
        if self.clock.epoch <= 0:
            return
        current = calendar_week_id(self.clock.epoch, tz=self.clock.tz)
        new_id = maybe_apply_week_reset(
            self.zones,
            week_id_shadow=self.week_id_shadow,
            current_week_id=current,
        )
        if new_id != self.week_id_shadow:
            self.week_id_shadow = new_id
            self.last_served_zone_id = 0

    def _emit(self, kind: EventType, detail: str = "", zone_id: int = 0) -> None:
        self._events.append(SimEvent(self.clock.epoch, kind, detail, zone_id))

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

    def _run_pre_flight(self, is_schedule: bool) -> Generator:
        yield from self._delay(1)
        result = evaluate_preflight(
            self.config,
            PreflightContext(
                now_epoch=self.clock.epoch,
                rain_sensor_last_wet_epoch=self.rain_sensor_last_wet_epoch,
                any_alarm_latched=self.any_alarm_latched,
                static_pressure_psi=self.pressure.read(zone_on=False)
                if self.currently_on_zone == 0 and self.config.gate_static_pressure_preflight
                else None,
            ),
            is_schedule=is_schedule,
        )
        self.pre_flight_passed = result.passed
        self.pre_flight_reason = result.reason

        if not self.pre_flight_passed:
            if result.benign:
                self._emit(EventType.PREFLIGHT_SKIP, self.pre_flight_reason)
            else:
                self._emit(EventType.PREFLIGHT_FAIL, self.pre_flight_reason)
                self._signal_alarm("preflight", blocking=False)
        yield Tick

    def _integrate_run_gallons(self) -> float:
        ppg = self.flow.pulses_per_gallon
        run_pulses = max(0, self.flow.pulses_total - self.run.started_pulses)
        return run_pulses / ppg if ppg > 0 else 0.0

    def _sync_weekly_delivered(self, zone_id: int, gallons_done: float) -> None:
        if self.ha_weekly_feed_valid():
            return
        zs = self.zones[zone_id - 1]
        zs.weekly_delivered_shadow = max(zs.weekly_delivered_shadow, gallons_done)

    def _finish_attempt(self, zone_id: int, *, completed: bool) -> None:
        now = self.clock.epoch
        zs = self.zones[zone_id - 1]
        if now > 0:
            zs.last_attempt_epoch = now
        if completed and now > 0:
            zs.last_finished_epoch = now

    def _run_zone_weekly_gallons(self, zone_id: int) -> Generator:
        zcfg = self.config.zone(zone_id)
        zs = self.zones[zone_id - 1]
        goal = zcfg.weekly_goal_gallons
        rain_mm = effective_rain_mm_this_week(self.config, now_epoch=self.clock.epoch)
        run_goal = effective_weekly_goal(
            goal,
            rain_mm,
            mm_per_step=self.config.rain_credit_mm_per_step,
            gallons_per_step=self.config.rain_credit_gallons_per_zone_per_step,
        )
        run_start = weekly_delivered_effective(zs, ha_feed_valid=self.ha_weekly_feed_valid())

        self.stamp_cadence_on_zone_off = True
        self.run = RunState(
            zone_id=zone_id,
            goal_gallons=run_goal,
            gallons_done=run_start,
            run_start_delivered=run_start,
            started_pulses=self.flow.pulses_total,
            started_ms=self.clock.boot_ms,
        )

        p = self.pressure.read(zone_on=False)
        if p < zcfg.start_minimum_psi or p > zcfg.start_maximum_psi:
            self.run.cancel_requested = True
            self.run.cancel_cause = "start_pressure_out_of_bounds"

        self.current_phase = "running"
        self._drive_zone(zone_id, True)

        while self.run.gallons_done < run_goal and not self.run.cancel_requested:
            yield from self._delay(1)
            session_gal = self._integrate_run_gallons()
            self.run.gallons_done = run_start + session_gal
            self._sync_weekly_delivered(zone_id, self.run.gallons_done)

        completed = not self.run.cancel_requested and self.run.gallons_done >= run_goal
        self.stamp_cadence_on_zone_off = completed
        self._drive_zone(zone_id, False)
        self._finish_attempt(zone_id, completed=completed)
        if self.ha_weekly_feed_valid():
            self.on_zone_weekly_delivered(zone_id, self.run.gallons_done)

        if self.run.cancel_requested:
            self._emit(EventType.RUN_CANCEL, self.run.cancel_cause, zone_id)
        else:
            self._emit(EventType.RUN_COMPLETE, zone_id=zone_id)

    def _set_schedule_gate(self, reason: str, due_zone: int = 0) -> None:
        self.schedule_gate_reason = reason
        self.schedule_gate_due_zone = due_zone

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

        yield from self._run_zone_weekly_gallons(zid)

        self.current_phase = "fault" if self.any_alarm_latched else "idle"
        self.phase_total_ms = 0
        if self.config.time_synced:
            self.last_run_finished_epoch = self.clock.epoch

        if self.run.cancel_requested:
            self.last_run_outcome = f"cancelled_{self.run.cancel_cause}"
        else:
            self.last_run_outcome = "completed"
        self.due_zone_id = 0

    def _update_plan_readout(self) -> None:
        if self.currently_on_zone != 0:
            return
        self._apply_week_reset_if_needed()
        feed_valid = self.ha_weekly_feed_valid()
        if not feed_valid:
            self.tracking_source = "local"
            self.config.tracking_source = "local"
        update_scheduled_next_epochs(
            self.config,
            self.zones,
            now_epoch=self.clock.epoch,
            tz=self.clock.tz,
            ha_time_valid=self.config.time_synced,
            ha_feed_valid=feed_valid,
        )
        self._emit(EventType.PLAN_UPDATED)

    def _weekly_budget_evaluator(self) -> None:
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

        if not in_watering_window(
            hour=self.clock.hour,
            minute=self.clock.minute,
            start_hour=cfg.schedule_start_hour,
            start_minute=cfg.schedule_start_minute,
            end_hour=cfg.schedule_end_hour,
            end_minute=cfg.schedule_end_minute,
        ):
            self._set_schedule_gate("outside_watering_window")
            return
        if is_blackout_day(
            dow_mon0=self.clock.dow_mon0,
            blackout_weekday_bitmask=cfg.blackout_weekday_bitmask,
        ):
            self._set_schedule_gate("blackout_day")
            return

        picked = self.next_pick_zone()
        if picked == 0:
            self._set_schedule_gate("nothing_eligible")
            return

        if self.skip_next_run_pending:
            self.skip_next_run_pending = False
            self._set_schedule_gate("skip_next_run", picked)
            return

        self._set_schedule_gate("none", picked)
        self.last_served_zone_id = picked
        self.config.last_served_zone_id = picked
        self.due_zone_id = picked
        self._start_script("schedule_fire_handler", self._schedule_fire_handler())

    def _signal_alarm(self, name: str, blocking: bool = False) -> None:
        self.alarms.add(name)
        self._emit(EventType.ALARM, name)
        if blocking:
            self.any_alarm_latched = True

    def _run_safety_1s(self, now_ms: int) -> None:
        cfg = self.config
        gpm = self.flow.gpm

        if cfg.gate_alarm_phantom_flow and self.currently_on_zone == 0 and gpm > cfg.phantom_flow_gpm:
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

    def _run_attempt_cap_1s(self, now_ms: int) -> None:
        if self.currently_on_zone == 0:
            return
        elapsed_ms = now_ms - self.zone_started_at_ms
        cap_ms = int(self.config.max_attempt_minutes * 60 * 1000)
        if elapsed_ms >= cap_ms:
            zid = self.currently_on_zone
            self.run.cancel_requested = True
            self.run.cancel_cause = "attempt_cap"
            self._signal_alarm("runtime_exceeded")
