"""HA ↔ ESP integration: weekly gallons, last-watering, firmware config profile."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from nedorachio.profile_bridge import load_repo_profile, operational_config_from_profile
from nedorachio.models import OperationalConfig, ZoneRuntimeState
from nedorachio.schedule import compute_zone_plans

from tests.controller.ha_integration_contract import (
    all_ha_integration_violations,
    extract_firmware_config_profile_json,
)


def test_ha_integration_contracts():
    violations = all_ha_integration_violations()
    assert not violations, "HA integration violations:\n- " + "\n- ".join(violations)


def test_firmware_config_profile_zone1_weekly_goal():
    profile = extract_firmware_config_profile_json()
    assert profile["zones"]["1"]["weekly_goal_gallons"] == 400


def test_weekly_plan_shows_remaining_when_under_goal():
    tz = ZoneInfo("America/New_York")
    now = int(datetime(2026, 5, 24, 15, 0, tzinfo=tz).timestamp())
    cfg = operational_config_from_profile(load_repo_profile())
    cfg.ha_weekly_feed_valid = True
    zones = [ZoneRuntimeState(weekly_delivered_shadow=50.0, ha_weekly_delivered=50.0) for _ in range(8)]

    plans = compute_zone_plans(
        cfg, zones, now_epoch=now, tz=tz, ha_time_valid=True, ha_feed_valid=True
    )
    assert plans[1].weekly_remaining_gallons == 350.0
    assert not plans[1].weekly_goal_met


def test_weekly_plan_blocked_when_goal_met():
    tz = ZoneInfo("America/New_York")
    now = int(datetime(2026, 5, 24, 15, 0, tzinfo=tz).timestamp())
    cfg = operational_config_from_profile(load_repo_profile())
    zones = [ZoneRuntimeState(weekly_delivered_shadow=400.0) for _ in range(8)]

    plans = compute_zone_plans(cfg, zones, now_epoch=now, tz=tz, ha_time_valid=True)
    assert plans[1].weekly_goal_met
    assert plans[1].blocked_reason == "weekly_goal_met"
