"""Regression tests for recent firmware/HA integration fixes."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from nedorachio.models import ZoneRuntimeState
from nedorachio.profile_bridge import load_repo_profile, operational_config_from_profile
from nedorachio.schedule import update_scheduled_next_epochs

from tests.controller.firmware_contract import (
    check_component_loads_config_profile_from_yaml,
    check_fallback_schedule_switch_reflects_engine,
)
from tests.controller.ha_integration_contract import (
    check_ha_dashboard_master_schedule,
    check_ha_dashboard_no_config_sync,
    check_ha_gallons_tracking,
    check_ha_no_config_sync,
    extract_firmware_config_profile_json,
)


def test_config_sync_removal_contracts():
    violations = (
        check_ha_no_config_sync()
        + check_ha_dashboard_no_config_sync()
        + check_component_loads_config_profile_from_yaml()
    )
    assert not violations, "Config sync removal violations:\n- " + "\n- ".join(violations)


def test_master_schedule_switch_contract():
    violations = check_fallback_schedule_switch_reflects_engine() + check_ha_dashboard_master_schedule()
    assert not violations, "Master schedule switch violations:\n- " + "\n- ".join(violations)


def test_firmware_yaml_profile_drives_operational_config():
    profile = load_repo_profile()
    cfg = operational_config_from_profile(profile)
    assert cfg.zones[0].weekly_goal_gallons == 400
    assert cfg.zones[4].weekly_goal_gallons == 100
    assert cfg.max_attempt_minutes == 30
    assert cfg.fallback_schedule_enabled is True


def test_firmware_profile_json_matches_operational_config_goals():
    raw = extract_firmware_config_profile_json()
    cfg = operational_config_from_profile(load_repo_profile())
    for zid in range(1, 9):
        key = str(zid)
        assert cfg.zones[zid - 1].weekly_goal_gallons == raw["zones"][key]["weekly_goal_gallons"]


def test_master_schedule_off_skips_next_run_planning():
    tz = ZoneInfo("America/New_York")
    now = int(datetime(2026, 5, 24, 15, 0, tzinfo=tz).timestamp())
    cfg = operational_config_from_profile(load_repo_profile())
    cfg.fallback_schedule_enabled = False
    zones = [ZoneRuntimeState() for _ in range(8)]

    update_scheduled_next_epochs(cfg, zones, now_epoch=now, tz=tz, ha_time_valid=True)
    assert all(z.scheduled_next_epoch == 0 for z in zones)


def test_master_schedule_on_plans_eligible_zone():
    tz = ZoneInfo("America/New_York")
    now = int(datetime(2026, 5, 24, 15, 0, tzinfo=tz).timestamp())
    cfg = operational_config_from_profile(load_repo_profile())
    zones = [ZoneRuntimeState(weekly_delivered_shadow=0.0) for _ in range(8)]

    update_scheduled_next_epochs(cfg, zones, now_epoch=now, tz=tz, ha_time_valid=True)
    assert zones[0].scheduled_next_epoch == now


def test_gallons_tracking_contract():
    violations = check_ha_gallons_tracking()
    assert not violations, "Gallons tracking violations:\n- " + "\n- ".join(violations)
