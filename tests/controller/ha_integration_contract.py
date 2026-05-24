"""
Static checks for HA ↔ ESP integration (last-watering helpers, firmware config profile).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HA_PACKAGE = REPO_ROOT / "homeassistant" / "packages" / "nedorachio.yaml"
HA_DASHBOARD = REPO_ROOT / "homeassistant" / "packages" / "nedorachio-dashboard.yaml"
FIRMWARE_COMPONENT = REPO_ROOT / "firmware" / "packages" / "10-nedorachio-component.yaml"
FIRMWARE_CONFIG = REPO_ROOT / "firmware" / "packages" / "11-config-profile.yaml"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


from nedorachio.profile_bridge import load_repo_profile_json


def extract_firmware_config_profile_json(yaml_text: str | None = None) -> dict:
    if yaml_text is not None:
        raise NotImplementedError("Pass yaml_text only in dedicated tests; use firmware file by default")
    return load_repo_profile_json()


def check_ha_no_config_sync() -> list[str]:
    text = _read(HA_PACKAGE)
    violations: list[str] = []
    forbidden = [
        "nedorachio_sync_controller",
        "nedorachio_config_sync_status",
        "nedorachio_apply_config_profile",
        "config_chunk_",
        "sensor.nedorachio_config_profile",
    ]
    for token in forbidden:
        if token in text:
            violations.append(f"HA package should not reference removed config sync: {token}")
    return violations


def check_ha_last_watering_helpers() -> list[str]:
    text = _read(HA_PACKAGE)
    violations: list[str] = []
    for z in range(1, 9):
        if f"nedorachio_zone_{z}_last_watering:" not in text:
            violations.append(f"Missing input_text.nedorachio_zone_{z}_last_watering")
        if f"nedorachio_zone_{z}_last_watering_epoch" not in text:
            violations.append(f"Missing template sensor nedorachio_zone_{z}_last_watering_epoch")
    return violations


def check_firmware_ha_last_watering_sensors() -> list[str]:
    text = _read(FIRMWARE_COMPONENT)
    violations: list[str] = []
    for z in range(1, 9):
        entity = f"sensor.nedorachio_zone_{z}_last_watering_epoch"
        if entity not in text:
            violations.append(f"Firmware missing homeassistant sensor for {entity}")
        if f"on_zone_last_watering({z}," not in text:
            violations.append(f"Firmware missing on_zone_last_watering handler for zone {z}")
    return violations


def check_firmware_config_profile_present() -> list[str]:
    violations: list[str] = []
    if not FIRMWARE_CONFIG.is_file():
        return ["Missing firmware/packages/11-config-profile.yaml"]
    text = _read(FIRMWARE_CONFIG)
    if "config_profile:" not in text:
        violations.append("11-config-profile.yaml must define nedorachio.config_profile")
    if "config_chunk_" in text:
        violations.append("Firmware config package must not define config_chunk entities")
    try:
        profile = extract_firmware_config_profile_json()
    except (ValueError, json.JSONDecodeError) as exc:
        violations.append(f"Invalid config_profile JSON: {exc}")
        return violations
    if profile.get("version") != 1:
        violations.append("config_profile version must be 1")
    zones = profile.get("zones")
    if not isinstance(zones, dict) or "1" not in zones:
        violations.append("config_profile must include zones.1")
    return violations


def check_firmware_no_config_chunks() -> list[str]:
    text = _read(FIRMWARE_COMPONENT)
    violations: list[str] = []
    if re.search(r"id: config_chunk_\d+", text):
        violations.append("Firmware component package must not define config_chunk entities")
    if "on_config_chunk_envelope" in text:
        violations.append("Firmware must not reference on_config_chunk_envelope")
    if "config_text_id" in text:
        violations.append("Firmware must not reference config_text_id (use config_profile in YAML)")
    return violations


def check_nedorachio_yaml_includes_config_package() -> list[str]:
    entry = REPO_ROOT / "firmware" / "nedorachio.yaml"
    text = _read(entry)
    if "11-config-profile.yaml" not in text:
        return ["firmware/nedorachio.yaml must include packages/11-config-profile.yaml"]
    return []


def check_ha_dashboard_no_config_sync() -> list[str]:
    text = _read(HA_DASHBOARD)
    violations: list[str] = []
    forbidden = [
        "nedorachio_sync_controller",
        "nedorachio_config_sync_status",
        "controller_online",
        "Sync config",
    ]
    for token in forbidden:
        if token in text:
            violations.append(f"Dashboard should not reference removed config sync: {token}")
    return violations


def check_ha_dashboard_master_schedule() -> list[str]:
    text = _read(HA_DASHBOARD)
    violations: list[str] = []
    if "fallback_schedule_enabled" not in text:
        violations.append("Dashboard must expose fallback_schedule_enabled as Master schedule")
    if "Master schedule" not in text:
        violations.append('Dashboard must label fallback_schedule_enabled as "Master schedule"')
    return violations


def all_ha_integration_violations() -> list[str]:
    checks = [
        check_ha_no_config_sync,
        check_ha_last_watering_helpers,
        check_firmware_ha_last_watering_sensors,
        check_firmware_config_profile_present,
        check_firmware_no_config_chunks,
        check_nedorachio_yaml_includes_config_package,
        check_ha_dashboard_no_config_sync,
        check_ha_dashboard_master_schedule,
    ]
    violations: list[str] = []
    for check in checks:
        violations.extend(check())
    return violations
