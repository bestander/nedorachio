"""
E2E tests: scheduled time + valve state inputs drive the state machine.

Regression target: schedule/plan visible in HA but valves never open on time.
"""

from __future__ import annotations

import pytest

from tests.controller.harness import IrrigationHarness, dt_epoch
from tests.controller.schedule_runner import (
    ScheduleCheckpoint,
    ScheduleInput,
    ScheduleScenario,
    ValveSnapshot,
    planned_fire_scenario,
    run_schedule_scenario,
)
from nedorachio.models import EventType


class TestScheduleValveStateMachine:
    """Time + valve snapshots in; state machine out; valves must match schedule."""

    def test_due_zone_valve_opens_at_planned_time(self):
        """Plan readout shows a start time — valve must actually open then."""
        scenario = planned_fire_scenario(zones_enabled=1, zones_due=(1,))
        h = run_schedule_scenario(scenario)

        zone_on = h.events_of(EventType.ZONE_ON)
        assert zone_on, f"Expected ZONE_ON event, snapshot={h.snapshot()}"
        assert zone_on[0].zone_id == 1

        planned = h.planned_start(1)
        fire = h.events_of(EventType.SCHEDULE_FIRE)[0]
        assert abs(fire.at_epoch - planned) <= 120, (
            f"Fire at {fire.at_epoch} too far from plan {planned}"
        )

    def test_two_zones_due_only_lowest_opens_first(self):
        start = dt_epoch(2026, 6, 3, 6, 0)
        scenario = ScheduleScenario(
            name="serial_due_zones",
            zones_enabled=2,
            inputs=ScheduleInput(
                epoch=start,
                zones_due=frozenset({1, 2}),
            ),
            checkpoints=[
                ScheduleCheckpoint(
                    advance_seconds=30,
                    expect_valves=ValveSnapshot(),
                    label="both due, plan only — valves still closed",
                ),
                ScheduleCheckpoint(
                    advance_seconds=65,
                    expect_valves=ValveSnapshot(open_zones=frozenset({1})),
                    expect_running_zone=1,
                    expect_phase="running",
                    expect_schedule_fire=True,
                    label="first eval: zone 1 opens, zone 2 waits",
                ),
                ScheduleCheckpoint(
                    advance_seconds=180,
                    expect_valves=ValveSnapshot(),
                    expect_running_zone=0,
                    expect_phase="idle",
                    label="zone 1 completes, valve closes",
                ),
                ScheduleCheckpoint(
                    advance_seconds=65,
                    expect_valves=ValveSnapshot(open_zones=frozenset({2})),
                    expect_running_zone=2,
                    expect_phase="running",
                    label="next eval: zone 2 opens",
                ),
            ],
        )
        run_schedule_scenario(scenario)

    def test_due_outside_window_plan_exists_valves_stay_closed(self):
        """Schedule plan may show a future window slot — no valve open outside window."""
        start = dt_epoch(2026, 6, 3, 12, 0)  # noon
        scenario = ScheduleScenario(
            name="outside_window",
            zones_enabled=1,
            config_overrides={
                "schedule_start_hour": 8,
                "schedule_end_hour": 10,
            },
            inputs=ScheduleInput(
                epoch=start,
                zones_due=frozenset({1}),
            ),
            checkpoints=[
                ScheduleCheckpoint(
                    advance_seconds=30,
                    expect_valves=ValveSnapshot(),
                    label="plan may exist for next window",
                ),
                ScheduleCheckpoint(
                    advance_seconds=3600,
                    expect_valves=ValveSnapshot(),
                    expect_running_zone=0,
                    label="one hour later — still outside window, valve closed",
                ),
            ],
        )
        h = run_schedule_scenario(scenario)
        assert not h.events_of(EventType.SCHEDULE_FIRE)
        assert not h.events_of(EventType.ZONE_ON)
        assert h.next_due_zone == 1

    def test_inside_window_after_plan_valve_opens_on_eval_tick(self):
        """Step through time checkpoints: closed until eval, then due valve opens."""
        window_start = dt_epoch(2026, 6, 3, 8, 0)
        scenario = ScheduleScenario(
            name="window_opens",
            zones_enabled=1,
            config_overrides={
                "schedule_start_hour": 8,
                "schedule_end_hour": 10,
            },
            inputs=ScheduleInput(
                epoch=window_start - 1800,  # 7:30 — before window
                zones_due=frozenset({1}),
            ),
            checkpoints=[
                ScheduleCheckpoint(
                    advance_seconds=1800,
                    expect_valves=ValveSnapshot(),
                    expect_running_zone=0,
                    label="7:30→8:00 window opens but eval not yet",
                ),
                ScheduleCheckpoint(
                    advance_seconds=65,
                    expect_valves=ValveSnapshot(open_zones=frozenset({1})),
                    expect_running_zone=1,
                    expect_phase="running",
                    expect_schedule_fire=True,
                    label="8:00+ eval: valve opens",
                ),
            ],
        )
        run_schedule_scenario(scenario)

    def test_blackout_friday_plan_never_shows_friday_night(self):
        """Plan readout must not assign Friday slots on a blackout day."""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/New_York")
        start = dt_epoch(2026, 5, 22, 23, 45)  # Friday night
        h = IrrigationHarness.fast_test(zones=4)
        h.config.schedule_start_hour = 23
        h.config.schedule_end_hour = 9
        h.config.schedule_end_minute = 0
        h.config.blackout_weekday_bitmask = (1 << 3) | (1 << 4)  # thu, fri
        h.config.maximum_runtime_minutes = 60
        h.clock.epoch = start
        h.make_all_due([1, 2, 3, 4])
        h.advance(30)

        for zid, epoch in h.planned_starts().items():
            local = datetime.fromtimestamp(epoch, tz=tz)
            assert local.weekday() != 4, (
                f"Zone {zid} planned on Friday {local} during blackout: {h.planned_starts()}"
            )

        h.advance(35)  # cadence evaluator tick on blackout Friday
        assert h.sim.schedule_gate_reason == "blackout_day"
        assert h.sim.schedule_gate_due_zone == 1
        assert not h.events_of(EventType.SCHEDULE_FIRE)

    def test_due_zone_plan_stable_within_minute(self):
        """Due zone plan must not slide forward every 30s plan tick."""
        from datetime import datetime
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/New_York")
        start = dt_epoch(2026, 5, 23, 23, 4)
        h = IrrigationHarness.fast_test(zones=4)
        h.config.schedule_start_hour = 23
        h.config.schedule_end_hour = 9
        h.config.maximum_runtime_minutes = 60
        h.clock.epoch = start
        h.make_all_due([1])

        h.advance(30)
        plan_a = h.planned_start(1)
        h.advance(20)  # same minute
        plan_b = h.planned_start(1)
        assert plan_a == plan_b, (
            f"Plan drifted within minute: {datetime.fromtimestamp(plan_a, tz)} "
            f"-> {datetime.fromtimestamp(plan_b, tz)}"
        )

    def test_preflight_skip_keeps_valves_closed_until_pressure_recovers(self):
        """Low static pressure skips fire — valve stays closed; recovers next eval."""
        start = dt_epoch(2026, 6, 3, 6, 0)
        h = IrrigationHarness.fast_test(zones=1)
        h.clock.epoch = start
        h.make_zone_due(1)
        h.set_pressure_static(20.0)  # below min 30

        h.advance(65)
        assert not h.open_valves(), h.snapshot()
        assert any(
            e.detail == "pressure_too_low" for e in h.events_of(EventType.PREFLIGHT_SKIP)
        )
        assert not h.events_of(EventType.ZONE_ON)

        h.set_pressure_static(50.0)
        h.advance(65)
        h.advance_until_idle()
        assert h.last_run_outcome == "completed"
        assert h.events_of(EventType.ZONE_ON)


@pytest.mark.parametrize(
    "scenario",
    [
        planned_fire_scenario(zones_enabled=1, zones_due=(1,)),
        planned_fire_scenario(
            zones_enabled=2,
            zones_due=(1, 2),
            start=(2026, 6, 3, 7, 30),
        ),
    ],
    ids=["single_zone", "multi_zone_first_due"],
)
def test_parametric_planned_schedule_opens_valve(scenario: ScheduleScenario):
    """Data-driven: time + due zones in → correct valve opens at planned time."""
    h = run_schedule_scenario(scenario)
    first_due = min(scenario.inputs.zones_due)
    assert h.valve_is_open(first_due) or h.last_run_outcome == "completed"
    zone_on_zones = {e.zone_id for e in h.events_of(EventType.ZONE_ON)}
    assert first_due in zone_on_zones
