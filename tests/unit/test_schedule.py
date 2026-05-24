from datetime import datetime
from zoneinfo import ZoneInfo

from nedorachio.models import ZoneRuntime
from nedorachio.schedule import compute_watering_schedule


FIXTURE = {
    "version": 1,
    "global": {
        "watering_window": {"start": "23:00", "end": "09:00", "timezone": "America/New_York"},
        "rain_accumulation_threshold_mm_48h": 5,
        "rain_accumulation_hold_hours_after_threshold": 24,
        "attempt_cooldown_minutes": 20,
        "maximum_runtime_minutes": 60,
        "no_flow_grace_seconds": 60,
        "no_flow_sustain_seconds": 30,
        "blackout": {"weekdays": ["thu", "fri"]},
    },
    "zones": {
        "1": {
            "enabled": True,
            "mode": "gallons_target",
            "goal_gallons_per_cycle": 400,
            "cycle_gallons": 200,
            "soak_minutes": 15,
            "minimum_interval_hours": 72,
            "start_minimum_psi": 35,
            "start_maximum_psi": 85,
            "minimum_running_psi": 20,
            "minimum_running_psi_grace_seconds": 60,
            "minimum_flow_gpm": 0.2,
            "maximum_flow_gpm": 12.0,
        }
    },
}


def _epoch(y, m, d, h, mi=0):
    return int(datetime(y, m, d, h, mi, tzinfo=ZoneInfo("America/New_York")).timestamp())


def test_zone_not_due_before_interval():
    runtime = {1: ZoneRuntime(last_finished_epoch=_epoch(2026, 6, 1, 6, 0))}
    plans = compute_watering_schedule(
        FIXTURE["global"],
        runtime,
        now_epoch=_epoch(2026, 6, 2, 6, 0),
        tz=ZoneInfo("America/New_York"),
        zones_enabled={1},
        min_interval_hours=72,
        maximum_runtime_minutes=60,
        start_hour=23,
        start_minute=0,
        end_hour=9,
        end_minute=0,
        blackout_weekdays=("thu", "fri"),
        fallback_start_epoch=_epoch(2026, 6, 1, 6, 0),
    )
    assert plans[1].blocked_reason == "not_due"


def test_due_zone_snaps_into_overnight_window():
    runtime = {1: ZoneRuntime(last_finished_epoch=_epoch(2026, 5, 28, 6, 0))}
    plans = compute_watering_schedule(
        FIXTURE["global"],
        runtime,
        now_epoch=_epoch(2026, 6, 1, 10, 0),
        tz=ZoneInfo("America/New_York"),
        zones_enabled={1},
        min_interval_hours=72,
        maximum_runtime_minutes=60,
        start_hour=23,
        start_minute=0,
        end_hour=9,
        end_minute=0,
        blackout_weekdays=("thu", "fri"),
        fallback_start_epoch=_epoch(2026, 6, 1, 6, 0),
    )
    assert plans[1].next_start_epoch is not None
    start_local = datetime.fromtimestamp(plans[1].next_start_epoch, ZoneInfo("America/New_York"))
    assert start_local.hour >= 23 or start_local.hour < 9


def test_blackout_pushes_off_blackout_day():
    runtime = {1: ZoneRuntime(last_finished_epoch=_epoch(2026, 5, 26, 6, 0))}
    plans = compute_watering_schedule(
        FIXTURE["global"],
        runtime,
        now_epoch=_epoch(2026, 6, 4, 6, 0),
        tz=ZoneInfo("America/New_York"),
        zones_enabled={1},
        min_interval_hours=72,
        maximum_runtime_minutes=60,
        start_hour=23,
        start_minute=0,
        end_hour=9,
        end_minute=0,
        blackout_weekdays=("thu", "fri"),
        fallback_start_epoch=_epoch(2026, 6, 1, 6, 0),
    )
    start_local = datetime.fromtimestamp(plans[1].next_start_epoch, ZoneInfo("America/New_York"))
    assert start_local.strftime("%a").lower() not in ("thu", "fri")


def test_never_run_zone_anchors_to_ha_now_not_fallback():
    """Zones with last_finished=0 should plan from HA time once synced, not hardcoded fallback."""
    from nedorachio.models import OperationalConfig, ZoneRuntimeState
    from nedorachio.schedule import compute_zone_plans, update_scheduled_next_epochs

    now = _epoch(2026, 5, 24, 15, 0)
    fallback = _epoch(2026, 6, 1, 11, 0)
    zones = [ZoneRuntimeState(last_finished_epoch=0)]
    config = OperationalConfig(
        zones_enabled_bitmask=1,
        fallback_schedule_enabled=True,
        schedule_start_hour=23,
        schedule_end_hour=9,
        maximum_runtime_minutes=60,
        fallback_start_epoch=fallback,
    )
    config.zones[0].min_interval_hours = 72

    plans = compute_zone_plans(
        config,
        zones,
        now_epoch=now,
        tz=ZoneInfo("America/New_York"),
        ha_time_valid=True,
    )
    start_local = datetime.fromtimestamp(plans[1].next_start_epoch, ZoneInfo("America/New_York"))
    assert start_local.year == 2026
    assert start_local.month == 5
    assert start_local.day >= 27

    zones_unsynced = [ZoneRuntimeState(last_finished_epoch=0)]
    update_scheduled_next_epochs(
        config,
        zones_unsynced,
        now_epoch=now,
        tz=ZoneInfo("America/New_York"),
        ha_time_valid=False,
    )
    assert zones_unsynced[0].scheduled_next_epoch == 0


def test_compute_zone_plans_empty_when_master_schedule_off():
    from nedorachio.models import OperationalConfig, ZoneRuntimeState
    from nedorachio.schedule import compute_zone_plans

    now = _epoch(2026, 5, 24, 15, 0)
    zones = [ZoneRuntimeState(last_finished_epoch=0)]
    config = OperationalConfig(
        zones_enabled_bitmask=1,
        fallback_schedule_enabled=False,
        schedule_start_hour=23,
        schedule_end_hour=9,
        maximum_runtime_minutes=60,
        fallback_start_epoch=_epoch(2026, 6, 1, 11, 0),
    )
    config.zones[0].min_interval_hours = 24

    plans = compute_zone_plans(
        config,
        zones,
        now_epoch=now,
        tz=ZoneInfo("America/New_York"),
        ha_time_valid=True,
    )
    assert plans == {}
