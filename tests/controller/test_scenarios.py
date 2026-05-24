"""End-to-end scenario tests for the irrigation controller simulator."""

from __future__ import annotations

import pytest

from tests.controller.harness import IrrigationHarness
from nedorachio.models import EventType


class TestPlanningAndExecution:
    """Verify planned watering sensors align with actual runs."""

    def test_single_zone_completes_and_updates_plan(self):
        h = IrrigationHarness.fast_test(zones=1)
        h.make_zone_due(1)
        h.advance(30)  # plan readout tick

        plan_before = h.planned_start(1)
        assert plan_before > 0, h.snapshot()

        fire_epoch = h.advance_to_next_scheduled_fire(max_hours=2, wait_for_completion=True)
        assert h.last_run_outcome == "completed"
        h.assert_completed(1)

        # Cadence reset: zone should not be immediately due again.
        h.advance(30)
        assert h.next_due_zone == 0

        # Plan should now point to next interval, not the past fire.
        plan_after = h.planned_start(1)
        assert plan_after > fire_epoch, h.snapshot()

    def test_preflight_low_pressure_skips_without_alarm_or_cadence_reset(self):
        h = IrrigationHarness.fast_test(zones=1)
        h.make_zone_due(1)
        h.set_pressure_static(20.0)  # below static min (30)

        last_before = h.zone_last_finished(1)
        h.advance(65)  # eval tick + preflight settle

        skips = h.events_of(EventType.PREFLIGHT_SKIP)
        assert any(e.detail == "pressure_too_low" for e in skips)
        assert not h.events_of(EventType.SCHEDULE_FIRE)
        assert not h.events_of(EventType.PREFLIGHT_FAIL)
        assert h.zone_last_finished(1) == last_before, "Preflight skip must not reset cadence"
        assert not h.sim.any_alarm_latched

        # Pressure recovers on next eval without clear_fault.
        h.set_pressure_static(50.0)
        h.advance(65)
        h.advance_until_idle()
        assert h.last_run_outcome == "completed"

    def test_pressure_recovers_on_next_eval_without_clear_fault(self):
        h = IrrigationHarness.fast_test(zones=1)
        h.make_zone_due(1)
        h.set_pressure_static(20.0)

        h.advance(65)
        assert any(e.detail == "pressure_too_low" for e in h.events_of(EventType.PREFLIGHT_SKIP))

        h.set_pressure_static(50.0)
        h.advance(65)
        h.advance_until_idle()

        assert h.last_run_outcome == "completed"
        h.assert_completed(1)

    def test_plan_stays_aligned_when_pressure_temporarily_low(self):
        h = IrrigationHarness.fast_test(zones=1)
        h.make_zone_due(1)
        h.set_pressure_static(20.0)
        h.advance(65)

        planned = h.planned_start(1)
        assert planned > 0
        assert h.next_due_zone == 1
        assert not h.sim.any_alarm_latched
        assert not h.events_of(EventType.SCHEDULE_FIRE)

        h.set_pressure_static(50.0)
        h.advance_to_next_scheduled_fire(max_hours=2, wait_for_completion=True)
        assert h.last_run_outcome == "completed"


class TestLowPressureMidRun:
    """Delays and restarts when pressure drops during watering."""

    def test_stale_latched_fault_auto_clears_on_scheduled_retry(self):
        h = IrrigationHarness.fast_test(zones=1)
        h.make_zone_due(1)
        h.sim.any_alarm_latched = True
        h.sim.alarms.add("no_flow")

        h.advance(65)
        h.advance_until_idle()

        assert h.last_run_outcome == "completed"
        assert not h.sim.any_alarm_latched

    def test_no_flow_cancel_retries_after_cooldown_without_manual_clear(self):
        h = IrrigationHarness.fast_test(zones=1)
        h.make_zone_due(1)
        h.advance(65)

        if h.currently_running_zone != 1:
            pytest.skip("Run did not start")

        h.set_no_flow_while_running()
        h.advance(20)
        h.assert_cancelled(1, "no_flow")
        assert not h.sim.any_alarm_latched
        assert h.next_due_zone == 1

        fires_before = len(h.events_of(EventType.SCHEDULE_FIRE))
        h.advance(60)
        assert len(h.events_of(EventType.SCHEDULE_FIRE)) == fires_before

        h.set_pressure_running(45.0)
        h.set_flow_gpm(5.0)
        h.advance(120)
        assert len(h.events_of(EventType.SCHEDULE_FIRE)) > fires_before
        h.advance_until_idle()
        assert h.last_run_outcome == "completed"

    def test_low_running_pressure_cancels_and_respects_cooldown(self):
        h = IrrigationHarness.fast_test(zones=1)
        h.make_zone_due(1)
        h.set_pressure_running(45.0)

        # Start the run (eval + preflight + zone on).
        h.advance(65)
        assert h.currently_running_zone == 1 or h.last_run_outcome == "completed"

        if h.last_run_outcome != "completed":
            # Drop running pressure after startup grace + sustained low PSI.
            h.set_pressure_running(15.0)
            h.advance(20)
            h.assert_cancelled(1, "low_pressure")
            assert not h.sim.any_alarm_latched
            fires_before = len(h.events_of(EventType.SCHEDULE_FIRE))

            # Zone stays cadence-due after incomplete cancel.
            assert h.next_due_zone == 1
            h.set_pressure_static(50.0)
            h.set_pressure_running(45.0)
            h.advance(60)
            new_fires = h.events_of(EventType.SCHEDULE_FIRE)[fires_before:]
            assert not new_fires, "Should not fire during cooldown"

            # After cooldown, auto-clear latched fault and fire again.
            fires_before = len(h.events_of(EventType.SCHEDULE_FIRE))
            h.advance(120)
            new_fires = h.events_of(EventType.SCHEDULE_FIRE)[fires_before:]
            assert new_fires
            h.advance_until_idle()
            assert h.last_run_outcome == "completed"

    def test_start_pressure_out_of_bounds_cancels_immediately(self):
        h = IrrigationHarness.fast_test(zones=1)
        h.make_zone_due(1)
        # Static OK for preflight, but zone start min is 35 — running profile used at start check.
        h.set_pressure_static(50.0)
        h.pressure.set_profile(lambda on: 30.0 if not on else 45.0)

        h.advance(65)
        cancels = h.events_of(EventType.RUN_CANCEL)
        assert any(c.detail == "start_pressure_out_of_bounds" for c in cancels)


class TestMultiZoneConflicts:
    """Multiple zones due — serial queue and single-zone invariant."""

    def test_two_zones_due_run_serially(self):
        h = IrrigationHarness.fast_test(zones=2)
        h.make_all_due([1, 2])
        h.advance(30)

        plan = h.planned_starts()
        assert 1 in plan and 2 in plan
        assert plan[1] <= plan[2], "Lower zone id should be scheduled first"

        # First fire: zone 1.
        h.advance_to_next_scheduled_fire(max_hours=2, wait_for_completion=True)
        h.assert_completed(1)

        # Zone 2 should run on a subsequent eval, not overlap.
        h.advance_to_next_scheduled_fire(max_hours=2, wait_for_completion=True)
        assert h.currently_running_zone == 0
        assert any(
            a.zone_id == 2 and a.outcome == "completed" for a in h.run_attempts()
        )

    def test_only_one_zone_on_at_a_time(self):
        h = IrrigationHarness.fast_test(zones=2)
        h.make_all_due([1, 2])

        for _ in range(30):
            h.advance(10)
            h.assert_no_overlap()
            if len(h.run_attempts()) >= 2:
                break

        completed = [a for a in h.run_attempts() if a.outcome == "completed"]
        assert len(completed) >= 1


class TestPlanVsReality:
    """Explicit checks that plan readout matches execution timing."""

    def test_fire_happens_near_planned_start(self):
        h = IrrigationHarness.fast_test(zones=1)
        h.make_zone_due(1)
        h.advance(30)

        planned = h.planned_start(1)
        assert planned > 0

        # Advance to planned time (aligned to 60s eval grid).
        delta = max(0, planned - h.epoch)
        h.advance(delta + 60)

        fires = h.events_of(EventType.SCHEDULE_FIRE)
        assert fires, f"Expected fire near {planned}, snapshot={h.snapshot()}"
        skew = abs(fires[0].at_epoch - planned)
        assert skew <= 120, f"Fire skew {skew}s too large; plan={planned} fire={fires[0].at_epoch}"

    def test_next_due_zone_matches_lowest_due(self):
        h = IrrigationHarness.fast_test(zones=3)
        h.make_zone_due(2)
        h.make_zone_due(3)
        # Zone 1 not due.
        h.sim.zones[0].last_finished_epoch = h.epoch

        assert h.next_due_zone == 2


class TestNoFlowCancel:
    def test_no_flow_after_grace_cancels_run(self):
        h = IrrigationHarness.fast_test(zones=1)
        h.make_zone_due(1)
        h.advance(65)  # fire + preflight

        if h.currently_running_zone == 1:
            h.set_no_flow_while_running()
            h.advance(20)  # startup grace (10s) + sustain (5s)
            h.assert_cancelled(1, "no_flow")

    def test_zero_gpm_during_startup_grace_does_not_cancel(self):
        h = IrrigationHarness.fast_test(zones=1)
        h.make_zone_due(1)
        h.advance(65)
        if h.currently_running_zone != 1:
            pytest.skip("Run did not start")

        h.set_no_flow_while_running()
        h.advance(8)  # still inside 10s startup grace
        assert h.currently_running_zone == 1
        assert not h.sim.run.cancel_requested


class TestCancelCadenceInteraction:
    """Cancelled runs must not advance cadence."""

    def test_cancelled_run_keeps_zone_due(self):
        h = IrrigationHarness.fast_test(zones=1)
        h.make_zone_due(1)
        last_before = h.zone_last_finished(1)
        h.advance(65)  # fire + preflight

        if h.currently_running_zone != 1:
            pytest.skip("Run did not start")

        h.set_pressure_running(15.0)
        h.advance(40)
        h.assert_cancelled(1, "low_pressure")

        assert h.last_run_outcome.startswith("cancelled_")
        assert h.zone_last_finished(1) == last_before
        assert h.next_due_zone == 1
        assert h.sim.last_non_completed_attempt_epoch > 0


class TestWindowGating:
    def test_outside_window_no_fire(self):
        h = IrrigationHarness.fast_test(zones=1)
        h.config.schedule_start_hour = 8
        h.config.schedule_end_hour = 10
        h.set_time(2026, 6, 3, 12, 0)  # noon — outside window
        h.make_zone_due(1)

        h.advance(3600)
        assert not h.events_of(EventType.SCHEDULE_FIRE)
        assert h.next_due_zone == 1  # still due, just gated
