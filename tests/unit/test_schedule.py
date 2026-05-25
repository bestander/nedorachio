from datetime import datetime
from zoneinfo import ZoneInfo

from nedorachio.models import OperationalConfig, ZoneRuntime, ZoneRuntimeState
from nedorachio.schedule import (
    accept_ha_weekly_update,
    calendar_week_id,
    compute_zone_plans,
    effective_weekly_goal,
    next_calendar_week_start_epoch,
    pick_next_zone_round_robin,
    rain_credit_gallons_per_zone,
    update_scheduled_next_epochs,
    weekly_delivered_effective,
    zone_has_weekly_deficit,
)


FIXTURE = {
    "version": 2,
    "global": {
        "watering_window": {"start": "23:00", "end": "09:00", "timezone": "America/New_York"},
        "rain_credit_mm_per_step": 10,
        "rain_credit_gallons_per_zone_per_step": 100,
        "rain_sensor_hold_hours_after_wet": 24,
        "attempt_cooldown_minutes": 20,
        "max_attempt_minutes": 30,
        "no_flow_grace_seconds": 60,
        "no_flow_sustain_seconds": 30,
        "blackout": {"weekdays": ["thu", "fri"]},
    },
    "zones": {
        "1": {
            "enabled": True,
            "weekly_goal_gallons": 100,
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


def test_weekly_delivered_effective_prefers_ha_when_feed_valid():
    zs = ZoneRuntimeState(weekly_delivered_shadow=999.0, ha_weekly_delivered=50.0)
    assert weekly_delivered_effective(zs, ha_feed_valid=True) == 50.0
    assert weekly_delivered_effective(zs, ha_feed_valid=False) == 999.0


def test_accept_ha_weekly_update_rejects_decrease():
    assert accept_ha_weekly_update(350.0, 0.0) is False
    assert accept_ha_weekly_update(350.0, 360.0) is True
    assert accept_ha_weekly_update(0.0, 0.0) is True


def test_calendar_week_id_monday_boundary():
    mon = _epoch(2026, 5, 25, 0, 0)
    tz = ZoneInfo("America/New_York")
    assert calendar_week_id(mon, tz=tz) == calendar_week_id(_epoch(2026, 5, 26, 12, 0), tz=tz)


def test_next_calendar_week_start_epoch_after_quota_met():
    tz = ZoneInfo("America/New_York")
    monday_morning = _epoch(2026, 6, 1, 10, 0)
    assert next_calendar_week_start_epoch(monday_morning, tz=tz) == _epoch(2026, 6, 8, 0, 0)
    sunday_night = _epoch(2026, 6, 7, 23, 59)
    assert next_calendar_week_start_epoch(sunday_night, tz=tz) == _epoch(2026, 6, 8, 0, 0)
    monday_midnight = _epoch(2026, 6, 1, 0, 0)
    assert next_calendar_week_start_epoch(monday_midnight, tz=tz) == monday_midnight


def test_zone_with_weekly_deficit_has_plan():
    runtime = {1: ZoneRuntime(weekly_delivered_shadow=40.0)}
    config = OperationalConfig(
        zones_enabled_bitmask=1,
        fallback_schedule_enabled=True,
        ha_weekly_feed_valid=True,
    )
    config.zones[0].weekly_goal_gallons = 100.0
    zones = [ZoneRuntimeState(weekly_delivered_shadow=40.0, ha_weekly_delivered=40.0)]
    plans = compute_zone_plans(
        config,
        zones,
        now_epoch=_epoch(2026, 6, 1, 10, 0),
        tz=ZoneInfo("America/New_York"),
        ha_feed_valid=True,
    )
    assert plans[1].weekly_remaining_gallons == 60.0
    assert plans[1].blocked_reason is None


def test_zone_at_weekly_goal_is_blocked():
    runtime = {1: ZoneRuntime(weekly_delivered_shadow=100.0)}
    config = OperationalConfig(zones_enabled_bitmask=1, fallback_schedule_enabled=True)
    config.zones[0].weekly_goal_gallons = 100.0
    zones = [ZoneRuntimeState(weekly_delivered_shadow=100.0)]
    plans = compute_zone_plans(
        config,
        zones,
        now_epoch=_epoch(2026, 6, 1, 10, 0),
        tz=ZoneInfo("America/New_York"),
    )
    assert plans[1].blocked_reason == "weekly_goal_met"
    assert plans[1].weekly_goal_met is True
    assert plans[1].next_eligible_epoch == _epoch(2026, 6, 8, 0, 0)


def test_round_robin_skips_zone_in_cooldown():
    config = OperationalConfig(
        zones_enabled_bitmask=0b11,
        attempt_cooldown_minutes=20.0,
        ha_weekly_feed_valid=True,
    )
    config.zones[0].weekly_goal_gallons = 100.0
    config.zones[1].weekly_goal_gallons = 100.0
    now = _epoch(2026, 6, 3, 6, 0)
    zones = [
        ZoneRuntimeState(weekly_delivered_shadow=0.0, last_attempt_epoch=now - 60),
        ZoneRuntimeState(weekly_delivered_shadow=0.0, last_attempt_epoch=0),
    ]
    assert pick_next_zone_round_robin(config, zones, now, ha_feed_valid=True) == 2


def test_compute_zone_plans_empty_when_master_schedule_off():
    now = _epoch(2026, 5, 24, 15, 0)
    zones = [ZoneRuntimeState()]
    config = OperationalConfig(
        zones_enabled_bitmask=1,
        fallback_schedule_enabled=False,
        schedule_start_hour=23,
        schedule_end_hour=9,
        max_attempt_minutes=30,
    )
    config.zones[0].weekly_goal_gallons = 100.0

    plans = compute_zone_plans(
        config,
        zones,
        now_epoch=now,
        tz=ZoneInfo("America/New_York"),
        ha_time_valid=True,
    )
    assert plans == {}


def test_update_scheduled_next_when_time_unsynced():
    now = _epoch(2026, 5, 24, 15, 0)
    zones = [ZoneRuntimeState()]
    config = OperationalConfig(zones_enabled_bitmask=1, fallback_schedule_enabled=True)
    config.zones[0].weekly_goal_gallons = 100.0
    update_scheduled_next_epochs(
        config,
        zones,
        now_epoch=now,
        tz=ZoneInfo("America/New_York"),
        ha_time_valid=False,
    )
    assert zones[0].scheduled_next_epoch == 0


def test_rain_credit_gallons_linear_ratio():
    assert rain_credit_gallons_per_zone(10.0, mm_per_step=10.0, gallons_per_step=100.0) == 100.0
    assert rain_credit_gallons_per_zone(25.0, mm_per_step=10.0, gallons_per_step=100.0) == 250.0


def test_rain_credit_reduces_weekly_remaining():
    tz = ZoneInfo("America/New_York")
    now = _epoch(2026, 5, 24, 15, 0)
    config = OperationalConfig(zones_enabled_bitmask=1, fallback_schedule_enabled=True)
    config.zones[0].weekly_goal_gallons = 400.0
    config.rain_mm_this_week = 20.0
    config.rain_mm_last_pushed_epoch = now
    zones = [ZoneRuntimeState(weekly_delivered_shadow=0.0, ha_weekly_delivered=0.0) for _ in range(8)]

    plans = compute_zone_plans(
        config,
        zones,
        now_epoch=now,
        tz=tz,
        ha_time_valid=True,
        ha_feed_valid=True,
    )
    assert plans[1].weekly_remaining_gallons == 200.0
    assert zone_has_weekly_deficit(config, zones[0], 1, ha_feed_valid=True, now_epoch=now)


def test_rain_credit_can_satisfy_weekly_goal():
    tz = ZoneInfo("America/New_York")
    now = _epoch(2026, 5, 24, 15, 0)
    config = OperationalConfig(zones_enabled_bitmask=1, fallback_schedule_enabled=True)
    config.zones[0].weekly_goal_gallons = 400.0
    config.rain_mm_this_week = 40.0
    config.rain_mm_last_pushed_epoch = now
    zones = [ZoneRuntimeState(weekly_delivered_shadow=0.0) for _ in range(8)]

    plans = compute_zone_plans(config, zones, now_epoch=now, tz=tz, ha_time_valid=True)
    assert plans[1].weekly_goal_met
    assert effective_weekly_goal(
        400.0,
        40.0,
        mm_per_step=config.rain_credit_mm_per_step,
        gallons_per_step=config.rain_credit_gallons_per_zone_per_step,
    ) == 0.0
