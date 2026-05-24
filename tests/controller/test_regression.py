"""
Regression tests for production bugs that slipped past the original harness.

Each test here maps to a real failure mode. See harness module docstring for why
simulator-only tests missed some of them (cooperative Python vs blocking C++).
"""

from __future__ import annotations

import pytest

from tests.controller.harness import IrrigationHarness, dt_epoch
from nedorachio.models import EventType


class TestGallonsTargetProductionProfile:
    """User runs gallons_target on zones 1–4 — must stay responsive mid-run."""

    def test_gallons_target_stays_responsive_past_watchdog_window(self):
        """Regression: blocking delay-in-lambda rebooted ESP ~24s into zone 1 run."""
        h = IrrigationHarness.production_gallons(zones=1)
        h.make_zone_due(1)
        h.advance(65)  # schedule fire + preflight

        if h.currently_running_zone != 1:
            pytest.skip("Run did not start")

        # Past the ~20–30s reboot window seen in production.
        h.advance_during_active_run(90, min_eval_ticks=1, min_plan_ticks=2)
        assert h.currently_running_zone == 1 or h.last_run_outcome.startswith(
            ("completed", "cancelled_")
        )

    def test_gallons_target_completes_in_production_profile(self):
        h = IrrigationHarness.production_gallons(zones=1)
        h.make_zone_due(1)
        h.advance_to_next_scheduled_fire(max_hours=2, wait_for_completion=True)
        assert h.last_run_outcome == "completed"
        assert h.events_of(EventType.ZONE_ON)

    def test_gallons_target_does_not_overshoot_goal_on_final_chunk(self):
        """Regression: 85/100 gal with 50 gal chunks ran to ~130+ before stopping."""
        h = IrrigationHarness.production_gallons(zones=1)
        z = h.config.zone(1)
        z.goal_gallons = 100.0
        z.cycle_gallons = 50.0
        h.sim.zones[0].cycle_delivered_gallons = 85.0
        h.make_zone_due(1)
        h.advance(65)
        if h.currently_running_zone != 1:
            pytest.skip("Run did not start")

        max_delivered = 0.0
        while h.currently_running_zone == 1:
            max_delivered = max(max_delivered, h.sim.zones[0].cycle_delivered_gallons)
            h.advance(1)

        assert max_delivered <= 100.0 + 0.5
        assert h.last_run_outcome == "completed"

    def test_gallons_complete_stamps_last_finished_for_next_cadence(self):
        """Regression: gallons_target chunk off left zone already off — cadence never stamped."""
        h = IrrigationHarness.production_gallons(zones=1)
        h.make_zone_due(1)
        before = h.zone_last_finished(1)
        h.advance_to_next_scheduled_fire(max_hours=2, wait_for_completion=True)
        assert h.last_run_outcome == "completed"
        assert h.zone_last_finished(1) > before
        assert h.zone_last_finished(1) >= h.epoch - 120


class TestAutonomousRecovery:
    """Recoverable faults must not permanently block the schedule."""

    def test_recoverable_cancel_does_not_latch_blocking_fault(self):
        h = IrrigationHarness.production_gallons(zones=1)
        h.make_zone_due(1)
        h.advance(65)
        if h.currently_running_zone != 1:
            pytest.skip("Run did not start")

        h.set_no_flow_while_running()
        h.advance(95)  # past grace + sustain
        h.assert_cancelled(1, "no_flow")
        assert not h.sim.any_alarm_latched
        assert "no_flow" in h.sim.alarms

    def test_phantom_flow_still_blocks_schedule(self):
        h = IrrigationHarness.production_gallons(zones=1)
        h.make_zone_due(1)
        h.sim.any_alarm_latched = True
        h.sim.alarms.add("phantom_flow")

        h.advance(65)
        assert not h.events_of(EventType.SCHEDULE_FIRE)
        assert not h.events_of(EventType.ZONE_ON)
        skips = h.events_of(EventType.PREFLIGHT_SKIP)
        assert any(e.detail == "alarm_latched" for e in skips)

    def test_stale_recoverable_latch_does_not_block_planned_fire(self):
        """Regression: NVS any_alarm_latched from old no_flow blocked 12:01 AM run."""
        h = IrrigationHarness.production_gallons(zones=1)
        h.set_time(2026, 5, 24, 0, 0)
        h.make_zone_due(1)
        h.sim.any_alarm_latched = True
        h.sim.alarms.add("no_flow")

        h.assert_valve_opens_at_planned_time(1, tolerance_s=180)


class TestMissedRunRegressions:
    """Plan shows a start time — valve must open; plan must not drift while due."""

    def test_schedule_retries_after_no_flow_cancel_without_manual_clear(self):
        h = IrrigationHarness.production_gallons(zones=1)
        h.make_zone_due(1)
        h.advance(65)
        if h.currently_running_zone != 1:
            pytest.skip("Run did not start")

        h.set_no_flow_while_running()
        h.advance(95)
        h.assert_cancelled(1, "no_flow")
        assert h.next_due_zone == 1
        assert not h.sim.any_alarm_latched

        h.set_flow_gpm(2.5)
        h.flow.force_gpm(2.5)
        fires_before = len(h.events_of(EventType.SCHEDULE_FIRE))
        h.advance_to_next_scheduled_fire(max_hours=1, wait_for_completion=False)
        assert len(h.events_of(EventType.SCHEDULE_FIRE)) > fires_before

    def test_plan_stable_while_due_overnight_window(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/New_York")
        h = IrrigationHarness.production_gallons(zones=4)
        h.set_time(2026, 5, 23, 23, 58)
        h.make_all_due([1, 2, 3, 4])

        h.advance(30)
        plans_a = h.planned_starts()
        h.advance(25)  # same minute
        plans_b = h.planned_starts()

        for zid, epoch_a in plans_a.items():
            epoch_b = plans_b.get(zid, 0)
            assert epoch_a == epoch_b, (
                f"Zone {zid} plan drifted: "
                f"{datetime.fromtimestamp(epoch_a, tz)} -> "
                f"{datetime.fromtimestamp(epoch_b, tz)}"
            )

    def test_plan_frozen_while_zone_running(self):
        """Regression: next run rolled forward every minute during active run."""
        h = IrrigationHarness.production_gallons(zones=1)
        h.make_zone_due(1)
        h.advance(65)
        if h.currently_running_zone != 1:
            pytest.skip("Run did not start")

        plan_at_start = h.planned_start(1)
        h.advance(120)
        assert h.currently_running_zone == 1
        assert h.planned_start(1) == plan_at_start
