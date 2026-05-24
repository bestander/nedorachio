"""HA ↔ ESP integration: last-watering contract, firmware config profile schedule."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from nedorachio.models import OperationalConfig, ZoneRuntimeState
from nedorachio.schedule import update_scheduled_next_epochs

from tests.controller.ha_integration_contract import (
    all_ha_integration_violations,
    extract_firmware_config_profile_json,
)


def test_ha_integration_contracts():
    violations = all_ha_integration_violations()
    assert not violations, "HA integration violations:\n- " + "\n- ".join(violations)


def test_firmware_config_profile_zone1_interval_24h():
    profile = extract_firmware_config_profile_json()
    assert profile["zones"]["1"]["minimum_interval_hours"] == 24


def test_schedule_uses_firmware_24h_profile():
    """With firmware profile (24h), zone 1 next run is May 25 not May 27 (72h boot default)."""
    tz = ZoneInfo("America/New_York")
    now = int(datetime(2026, 5, 24, 15, 0, tzinfo=tz).timestamp())
    profile = extract_firmware_config_profile_json()
    interval_h = profile["zones"]["1"]["minimum_interval_hours"]
    assert interval_h == 24

    zones = [ZoneRuntimeState(last_finished_epoch=0) for _ in range(8)]
    config = OperationalConfig(
        zones_enabled_bitmask=0x0F,
        fallback_schedule_enabled=True,
        schedule_start_hour=23,
        schedule_end_hour=9,
        blackout_weekday_bitmask=(1 << 3) | (1 << 4),
        maximum_runtime_minutes=60,
        fallback_start_epoch=int(datetime(2026, 6, 1, 11, 0, tzinfo=tz).timestamp()),
    )
    for i in range(4):
        config.zones[i].min_interval_hours = interval_h
        config.zones[i].enabled = True

    update_scheduled_next_epochs(config, zones, now_epoch=now, tz=tz, ha_time_valid=True)
    next_local = datetime.fromtimestamp(zones[0].scheduled_next_epoch, tz)
    assert next_local.day == 25
    assert next_local.month == 5


def test_boot_default_72h_produces_may_27():
    """Document C++ boot default when config_profile is missing or invalid."""
    tz = ZoneInfo("America/New_York")
    now = int(datetime(2026, 5, 24, 15, 0, tzinfo=tz).timestamp())
    zones = [ZoneRuntimeState(last_finished_epoch=0) for _ in range(8)]
    config = OperationalConfig(
        zones_enabled_bitmask=0x0F,
        fallback_schedule_enabled=True,
        schedule_start_hour=23,
        schedule_end_hour=9,
        blackout_weekday_bitmask=(1 << 3) | (1 << 4),
        maximum_runtime_minutes=60,
        fallback_start_epoch=int(datetime(2026, 6, 1, 11, 0, tzinfo=tz).timestamp()),
    )
    for i in range(4):
        config.zones[i].min_interval_hours = 72
        config.zones[i].enabled = True

    update_scheduled_next_epochs(config, zones, now_epoch=now, tz=tz, ha_time_valid=True)
    next_local = datetime.fromtimestamp(zones[0].scheduled_next_epoch, tz)
    assert next_local.day == 27
