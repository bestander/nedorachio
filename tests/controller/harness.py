"""
High-level test harness for irrigation controller E2E scenarios.

Provides a readable API over ControllerSimulator + mock sensors so tests
can express intent ("zone 1 is due, pressure drops mid-run") without
re-implementing firmware tick logic.

Testing strategy (two layers)
-----------------------------
1. **Simulator E2E** — exercises schedule/pre-flight/run logic with a
   cooperative script model (same semantics as fixed firmware, not same
   implementation). Catches plan drift, blackout gating, cooldown, recovery.

2. **Firmware contracts** (`firmware_contract.py`) — static checks on real
   ESPHome YAML. Catches ESP-specific anti-patterns the simulator cannot
   model (e.g. blocking ``delay()`` inside a C++ lambda while-loop).

Always add a firmware contract when fixing a bug that lived only in C++
script/lambda code. Add simulator E2E when fixing schedule/state-machine logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from tests.controller.config import ControllerConfig, ZoneConfig
from tests.controller.mock_sensors import MockFlow, MockPressure, MockTime
from tests.controller.simulator import BackgroundActivity, ControllerSimulator, EventType, SimEvent


def dt_epoch(
    year: int,
    month: int,
    day: int,
    hour: int = 0,
    minute: int = 0,
    tz: str = "America/New_York",
) -> int:
    """Wall-clock epoch for scenario setup."""
    return int(datetime(year, month, day, hour, minute, tzinfo=ZoneInfo(tz)).timestamp())


@dataclass
class RunAttempt:
    """One scheduled or attempted watering run."""

    started_epoch: int
    zone_id: int
    outcome: str  # completed | cancelled_* | preflight_fail
    finished_epoch: Optional[int] = None


class IrrigationHarness:
    """
    End-to-end harness wrapping the controller simulator.

    Typical usage::

        h = IrrigationHarness.fast_test(zones=2)
        h.set_time(2026, 6, 3, 6, 0)
        h.make_zone_due(1)
        h.advance_to_next_scheduled_fire()
        assert h.last_run_outcome == "completed"
    """

    def __init__(
        self,
        config: Optional[ControllerConfig] = None,
        clock: Optional[MockTime] = None,
        pressure: Optional[MockPressure] = None,
        flow: Optional[MockFlow] = None,
    ):
        self.config = config or ControllerConfig()
        self.clock = clock or MockTime(epoch=dt_epoch(2026, 6, 1, 6, 0))
        self.pressure = pressure or MockPressure(static_psi=50.0, running_psi=45.0)
        self.flow = flow or MockFlow(pulses_per_gallon=self.config.pulses_per_gallon)
        self.sim = ControllerSimulator(self.config, self.clock, self.pressure, self.flow)

    @classmethod
    def fast_test(cls, zones: int = 2, **overrides) -> "IrrigationHarness":
        """
        Build a harness with compressed timings for fast CI.

        - 1-minute cycles, no soak
        - 1-hour cadence (not 48h)
        - 2-minute attempt cooldown
        - Only `zones` enabled (bitmask)
        """
        zone_cfgs = []
        for i in range(8):
            z = ZoneConfig(
                total_min=2.0,
                cycle_min=1.0,
                soak_min=0.0,
                min_interval_hours=1.0,
            )
            zone_cfgs.append(z)

        bitmask = (1 << zones) - 1
        cfg = ControllerConfig(
            zones=zone_cfgs,
            zones_enabled_bitmask=bitmask,
            attempt_cooldown_minutes=2.0,
            maximum_runtime_minutes=5.0,
            no_flow_grace_s=10.0,
            no_flow_sustain_s=5.0,
            schedule_start_hour=0,
            schedule_end_hour=23,
            schedule_end_minute=59,
        )
        for k, v in overrides.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)

        start = dt_epoch(2026, 6, 3, 6, 0)
        return cls(
            config=cfg,
            clock=MockTime(epoch=start),
            pressure=MockPressure(static_psi=50.0, running_psi=45.0),
            flow=MockFlow(pulses_per_gallon=cfg.pulses_per_gallon, gpm_when_on=2.0),
        )

    @classmethod
    def production_gallons(cls, zones: int = 4, **overrides) -> "IrrigationHarness":
        """
        Match ``homeassistant/packages/nedorachio_config.yaml`` defaults.

        Uses compressed timings where safe (2-min cooldown, smaller gallon targets)
        but keeps ``schedule_mode=gallons`` and the overnight watering window so
        gallons-target regressions match production.
        """
        zone_cfgs = []
        for i in range(8):
            enabled = i < zones
            z = ZoneConfig(
                schedule_mode=1 if enabled else 0,
                goal_gallons=20.0 if enabled else 0.0,
                cycle_gallons=10.0 if enabled else 0.0,
                soak_min=0.0,  # keep CI fast; production uses 15
                min_interval_hours=72.0,
                min_flow_gpm=0.2,
                max_flow_gpm=12.0,
                minimum_running_psi_grace_seconds=60,
            )
            zone_cfgs.append(z)

        bitmask = (1 << zones) - 1
        cfg = ControllerConfig(
            zones=zone_cfgs,
            zones_enabled_bitmask=bitmask,
            attempt_cooldown_minutes=2.0,
            maximum_runtime_minutes=60.0,
            no_flow_grace_s=60.0,
            no_flow_sustain_s=30.0,
            schedule_start_hour=23,
            schedule_start_minute=0,
            schedule_end_hour=9,
            schedule_end_minute=0,
            blackout_weekday_bitmask=(1 << 3) | (1 << 4),  # thu, fri
        )
        for k, v in overrides.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)

        start = dt_epoch(2026, 5, 23, 23, 30)  # Saturday night, in window
        return cls(
            config=cfg,
            clock=MockTime(epoch=start),
            pressure=MockPressure(static_psi=50.0, running_psi=45.0),
            flow=MockFlow(pulses_per_gallon=cfg.pulses_per_gallon, gpm_when_on=2.5),
        )

    # ---------------------------------------------------------------- setup
    def set_time(self, year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> None:
        self.clock.epoch = dt_epoch(year, month, day, hour, minute, str(self.clock.tz))

    def make_zone_due(self, zone_id: int, hours_overdue: float = 0.1) -> None:
        """Set last_finished so zone is cadence-due now."""
        zcfg = self.config.zone(zone_id)
        interval_s = int(zcfg.min_interval_hours * 3600)
        overdue_s = int(hours_overdue * 3600)
        self.sim.zones[zone_id - 1].last_finished_epoch = (
            self.clock.epoch - interval_s - overdue_s
        )

    def make_all_due(self, zone_ids: Iterable[int]) -> None:
        for zid in zone_ids:
            self.make_zone_due(zid)

    def set_pressure_static(self, psi: float) -> None:
        self.pressure.set_static(psi)

    def set_pressure_running(self, psi: float) -> None:
        self.pressure.set_running(psi)

    def set_flow_gpm(self, gpm: float) -> None:
        self.flow.gpm_when_on = gpm

    def set_no_flow_while_running(self) -> None:
        """Simulate stuck valve / broken line."""
        self.flow.gpm_when_on = 0.0
        self.flow.force_gpm(0.0)

    def clear_fault(self) -> None:
        self.sim.clear_fault()

    def background_activity(self) -> BackgroundActivity:
        return self.sim.background_activity()

    def advance_during_active_run(
        self, seconds: int, *, min_eval_ticks: int = 1, min_plan_ticks: int = 1
    ) -> BackgroundActivity:
        """
        Advance while a zone valve is open; assert background tasks keep ticking.

        Proxy for ESP32 main-loop liveness during long gallons-target runs.
        """
        assert self.currently_running_zone > 0, (
            f"No active run to monitor: {self.snapshot()}"
        )
        before = self.background_activity()
        self.advance(seconds)
        after = self.background_activity()
        delta = BackgroundActivity(
            ticks=after.ticks - before.ticks,
            safety_ticks=after.safety_ticks - before.safety_ticks,
            eval_ticks=after.eval_ticks - before.eval_ticks,
            plan_ticks=after.plan_ticks - before.plan_ticks,
            ticks_while_zone_on=after.ticks_while_zone_on - before.ticks_while_zone_on,
        )
        assert delta.ticks == seconds, f"Expected {seconds}s advance, got {delta.ticks}"
        assert delta.safety_ticks == seconds, "1s safety interval must run every second"
        assert delta.ticks_while_zone_on == seconds, "Zone should stay on entire interval"
        assert delta.eval_ticks >= min_eval_ticks, (
            f"Cadence evaluator stalled during run (delta eval_ticks={delta.eval_ticks})"
        )
        assert delta.plan_ticks >= min_plan_ticks, (
            f"Plan readout stalled during run (delta plan_ticks={delta.plan_ticks})"
        )
        return delta

    def assert_valve_opens_at_planned_time(self, zone_id: int, tolerance_s: int = 120) -> int:
        """Plan must exist and schedule fire must open the valve near that epoch."""
        self.advance(30)  # plan readout
        planned = self.planned_start(zone_id)
        assert planned > 0, f"No plan for zone {zone_id}: {self.snapshot()}"

        fires_before = len(self.events_of(EventType.SCHEDULE_FIRE))
        delta = max(0, planned - self.epoch)
        self.advance(delta + 65)

        fires = self.events_of(EventType.SCHEDULE_FIRE)[fires_before:]
        assert fires, f"No schedule fire near plan {planned}: {self.snapshot()}"
        assert abs(fires[0].at_epoch - planned) <= tolerance_s

        zone_on = [e for e in self.events_of(EventType.ZONE_ON) if e.zone_id == zone_id]
        assert zone_on, f"Zone {zone_id} valve never opened: {self.snapshot()}"
        return fires[0].at_epoch

    # ----------------------------------------------------------------- motion
    def advance(self, seconds: int) -> None:
        self.sim.advance(seconds)

    def advance_minutes(self, minutes: float) -> None:
        self.advance(int(minutes * 60))

    def advance_hours(self, hours: float) -> None:
        self.advance(int(hours * 3600))

    def advance_until_idle(self, max_seconds: int = 600) -> None:
        """Advance until no zone is running and no script is active."""
        for _ in range(max_seconds):
            if self.currently_running_zone == 0 and not self.sim.is_script_running():
                return
            self.advance(1)

    def advance_to_next_scheduled_fire(
        self, max_hours: float = 24.0, wait_for_completion: bool = False
    ) -> int:
        """
        Advance time until a scheduled fire occurs or timeout.

        Returns epoch of the fire. Raises AssertionError on timeout.
        """
        deadline = self.clock.epoch + int(max_hours * 3600)
        fires_before = len(self.events_of(EventType.SCHEDULE_FIRE))

        while self.clock.epoch < deadline:
            self.advance(60)
            fires = self.events_of(EventType.SCHEDULE_FIRE)
            if len(fires) > fires_before:
                if wait_for_completion:
                    self.advance_until_idle()
                return fires[-1].at_epoch

        plan = self.planned_starts()
        raise AssertionError(
            f"No schedule fire within {max_hours}h. "
            f"next_due={self.next_due_zone}, plan={plan}, "
            f"preflight_fails={self.events_of(EventType.PREFLIGHT_FAIL)}"
        )

    # -------------------------------------------------------------- readouts
    @property
    def epoch(self) -> int:
        return self.clock.epoch

    @property
    def currently_running_zone(self) -> int:
        return self.sim.currently_on_zone

    @property
    def current_phase(self) -> str:
        return self.sim.current_phase

    @property
    def last_run_outcome(self) -> str:
        return self.sim.last_run_outcome

    @property
    def next_due_zone(self) -> int:
        return self.sim.next_due_zone()

    def planned_start(self, zone_id: int) -> int:
        return self.sim.zone_scheduled_next(zone_id)

    def planned_starts(self) -> dict[int, int]:
        return {
            zid: self.sim.zone_scheduled_next(zid)
            for zid in range(1, 9)
            if self.config.zone_enabled(zid) and self.sim.zone_scheduled_next(zid) > 0
        }

    def zone_last_finished(self, zone_id: int) -> int:
        return self.sim.zone_last_finished(zone_id)

    def valve_is_open(self, zone_id: int) -> bool:
        return self.sim.zones[zone_id - 1].actual_state

    def open_valves(self) -> set[int]:
        return {
            zid
            for zid in range(1, 9)
            if self.config.zone_enabled(zid) and self.valve_is_open(zid)
        }

    def events(self) -> list[SimEvent]:
        return self.sim.events

    def events_of(self, kind: EventType) -> list[SimEvent]:
        return [e for e in self.sim.events if e.kind == kind]

    def run_attempts(self) -> list[RunAttempt]:
        """Reconstruct run attempts from the event log."""
        attempts: list[RunAttempt] = []
        pending: dict[int, RunAttempt] = {}

        for ev in self.sim.events:
            if ev.kind == EventType.SCHEDULE_FIRE:
                pending[ev.zone_id] = RunAttempt(
                    started_epoch=ev.at_epoch, zone_id=ev.zone_id, outcome="running"
                )
            elif ev.kind == EventType.PREFLIGHT_FAIL:
                attempts.append(
                    RunAttempt(
                        started_epoch=ev.at_epoch,
                        zone_id=0,
                        outcome=f"preflight_{ev.detail}",
                    )
                )
            elif ev.kind == EventType.RUN_COMPLETE:
                att = pending.pop(ev.zone_id, None)
                if att:
                    att.outcome = "completed"
                    att.finished_epoch = ev.at_epoch
                    attempts.append(att)
            elif ev.kind == EventType.RUN_CANCEL:
                att = pending.pop(ev.zone_id, None)
                if att:
                    att.outcome = f"cancelled_{ev.detail}"
                    att.finished_epoch = ev.at_epoch
                    attempts.append(att)
        return attempts

    # -------------------------------------------------------------- assertions
    def assert_plan_covers_due_zones(self) -> None:
        """Every cadence-due zone should have a non-zero planned start."""
        due = self.next_due_zone
        if due == 0:
            return
        plan = self.planned_starts()
        assert due in plan or any(
            self.sim.zone_scheduled_next(z) > 0 for z in plan
        ), (
            f"Zone {due} is due but plan readout empty. "
            f"plan={plan}, phase={self.current_phase}"
        )

    def assert_no_overlap(self) -> None:
        """Single-zone invariant: at most one zone on."""
        assert self.currently_running_zone in range(0, 9)

    def assert_completed(self, zone_id: int) -> None:
        attempts = [a for a in self.run_attempts() if a.zone_id == zone_id]
        assert attempts, f"No run attempts for zone {zone_id}"
        assert attempts[-1].outcome == "completed", attempts[-1]

    def assert_cancelled(self, zone_id: int, cause: Optional[str] = None) -> None:
        attempts = [a for a in self.run_attempts() if a.zone_id == zone_id]
        assert attempts, f"No run attempts for zone {zone_id}"
        last = attempts[-1]
        assert last.outcome.startswith("cancelled_"), last
        if cause:
            assert last.outcome == f"cancelled_{cause}", last

    def snapshot(self) -> dict:
        """Point-in-time state for debugging failed tests."""
        return {
            "epoch": self.epoch,
            "running": self.currently_running_zone,
            "phase": self.current_phase,
            "outcome": self.last_run_outcome,
            "next_due": self.next_due_zone,
            "plan": self.planned_starts(),
            "last_finished": {
                z: self.zone_last_finished(z)
                for z in range(1, 9)
                if self.config.zone_enabled(z)
            },
        }
