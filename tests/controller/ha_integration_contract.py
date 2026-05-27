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
FIRMWARE_COMPONENT_CPP = REPO_ROOT / "firmware" / "components" / "nedorachio" / "nedorachio_component.cpp"
FIRMWARE_CONFIG = REPO_ROOT / "firmware" / "packages" / "11-config-profile.yaml"
# Dashboard shows only physically wired zones (package/firmware still define all 8).
DASHBOARD_ZONES = range(1, 5)


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
    if profile.get("version") != 2:
        violations.append("config_profile version must be 2")
    zones = profile.get("zones")
    if not isinstance(zones, dict) or "1" not in zones:
        violations.append("config_profile must include zones.1")
    g = profile.get("global", {})
    if "rain_credit_mm_per_step" not in g:
        violations.append("config_profile must define rain_credit_mm_per_step")
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


def check_ha_weekly_gallons_tracking() -> list[str]:
    pkg = _read(HA_PACKAGE)
    dash = _read(HA_DASHBOARD)
    fw = _read(FIRMWARE_COMPONENT)
    violations: list[str] = []
    for z in range(1, 9):
        if f"nedorachio_zone_{z}_week_baseline_gallons:" not in pkg:
            violations.append(f"Missing input_number.nedorachio_zone_{z}_week_baseline_gallons")
        if f"nedorachio_zone_{z}_gallons_lifetime:" not in pkg:
            violations.append(f"Missing input_number.nedorachio_zone_{z}_gallons_lifetime")
        if f"nedorachio_zone_{z}_weekly_delivered_last:" not in pkg:
            violations.append(f"Missing input_number.nedorachio_zone_{z}_weekly_delivered_last")
        if f"zone_{z}_weekly_goal_gallons_sensor" not in fw:
            violations.append(f"Firmware missing zone {z} weekly_goal_gallons sensor")
        if f"input_number.nedorachio_zone_{z}_weekly_goal_gallons:" in pkg:
            violations.append(f"weekly goals must come from device sensors, not HA input_number for zone {z}")
        if f"sensor.nedorachio_irrigation_controller_zone_{z}_weekly_goal_gallons" not in pkg:
            violations.append(f"HA weekly_remaining must reference device weekly goal sensor for zone {z}")
        if f"nedorachio_zone_{z}_weekly_delivered" not in pkg:
            violations.append(f"Missing template sensor nedorachio_zone_{z}_weekly_delivered")
        if f"sensor.nedorachio_zone_{z}_weekly_delivered" not in fw:
            violations.append(f"Firmware missing homeassistant sensor for zone {z} weekly_delivered")
        if f"on_zone_weekly_delivered({z}," not in fw:
            violations.append(f"Firmware missing on_zone_weekly_delivered handler for zone {z}")
    for z in DASHBOARD_ZONES:
        if f"sensor.nedorachio_zone_{z}_weekly_delivered" not in dash:
            violations.append(f"Dashboard must reference sensor.nedorachio_zone_{z}_weekly_delivered")
        if f"sensor.nedorachio_zone_{z}_weekly_remaining" in dash:
            violations.append(f"Dashboard must not show weekly_remaining for zone {z}")
    if "nedorachio_weekly_baseline_reset" not in pkg:
        violations.append("Missing Monday weekly baseline reset automation")
    if "nedorachio_sync_zone_gallons_lifetime" not in pkg:
        violations.append("Missing sync automation for HA-persisted zone gallons lifetime")
    if "nedorachio_persist_weekly_delivered_last" not in pkg:
        violations.append("Missing persist automation for last-known weekly delivered")
    if "lifetime >= baseline" not in pkg:
        violations.append("weekly_delivered must require lifetime >= baseline before publishing")
    fw_engine = REPO_ROOT / "firmware" / "components" / "nedorachio" / "engine.cpp"
    fw_schedule = REPO_ROOT / "firmware" / "components" / "nedorachio" / "schedule.cpp"
    if fw_engine.is_file():
        engine_text = _read(fw_engine)
        if "accept_ha_weekly_update" not in engine_text:
            violations.append("Firmware must reject stale HA weekly_delivered decreases")
        if "!this->is_manual_run_ && this->run_gallons_done_ >= this->run_goal_gallons_" not in engine_text:
            violations.append("Firmware must not stop manual runs when weekly goal is already met")
    if fw_schedule.is_file():
        schedule_text = _read(fw_schedule)
        if "next_schedule_opportunity_epoch" not in schedule_text:
            violations.append("Firmware must clamp scheduled next run to watering window")
    return violations


def check_ha_gallons_tracking() -> list[str]:
    pkg = _read(HA_PACKAGE)
    dash = _read(HA_DASHBOARD)
    fw = _read(FIRMWARE_COMPONENT)
    violations: list[str] = []
    for z in range(1, 9):
        device_total = f"sensor.nedorachio_irrigation_controller_zone_{z}_gallons_total"
        if device_total not in pkg:
            violations.append(f"HA package must mirror device gallons total {device_total}")
        if f"nedorachio_zone_{z}_gallons_lifetime_v1" not in pkg:
            violations.append(f"Missing template sensor nedorachio_zone_{z}_gallons_lifetime")
        if f"nedorachio_zone_{z}_gallons_7d_v1" not in pkg:
            violations.append(f"Missing 7-day display template sensor nedorachio_zone_{z}_gallons_7d")
        if f"nedorachio_zone_{z}_gallons_7d_rolling_v1" not in pkg:
            violations.append(f"Missing 7-day statistics sensor nedorachio_zone_{z}_gallons_7d_rolling")
        if f"zone_{z}_gallons_total_sensor" not in fw:
            violations.append(f"Firmware missing zone {z} gallons total sensor")
    for z in DASHBOARD_ZONES:
        if f"sensor.nedorachio_zone_{z}_gallons_last_7_days" not in dash:
            violations.append(f"Dashboard must reference sensor.nedorachio_zone_{z}_gallons_last_7_days")
    if "nedorachio_record_gallons_delivery" in pkg:
        violations.append("Gallons must not use HA delivery-event accumulation automations")
    if "utility_meter:" in pkg:
        violations.append("Use statistics sensors for rolling 7-day gallons, not utility_meter")
    if "platform: statistics" not in pkg:
        violations.append("7-day gallons must use statistics platform sensors")
    if "gallons_7d_rolling" not in pkg:
        violations.append("Missing internal statistics sensors for 7-day gallons rolling totals")
    if "recorder.get_statistics" in pkg:
        violations.append("Use statistics platform for 7-day gallons, not recorder.get_statistics triggers")
    if "statistics-graph" not in dash or "Gallons by zone (last 7 days)" not in dash:
        violations.append("Dashboard must include 7-day gallons statistics-graph")
    if "Gallons last 7 days (rolling)" not in dash:
        violations.append("Dashboard must show rolling 7-day gallons totals")
    if "Weekly progress" not in dash:
        violations.append("Dashboard must show weekly progress section")
    if "state_class: total_increasing" not in fw or "Zone 1 gallons total" not in fw:
        violations.append("Firmware zone gallons sensors must use state_class total_increasing")
    return violations


def check_ha_rain_week_wiring() -> list[str]:
    text = _read(HA_PACKAGE)
    fw = _read(FIRMWARE_COMPONENT)
    violations: list[str] = []
    if "sensor.openweathermap_rain_intensity" not in text:
        violations.append("HA package must expect sensor.openweathermap_rain_intensity as rain input")
    if "nedorachio_rain_intensity_input_v1" not in text:
        violations.append("Missing template sensor nedorachio_rain_intensity_input")
    if "nedorachio_rain_observed_week_v1" not in text:
        violations.append("Missing template sensor nedorachio_rain_observed_week")
    if "nedorachio_rain_credit_gallons_per_zone_v1" not in text:
        violations.append("Missing template sensor nedorachio_rain_credit_gallons_per_zone")
    if "nedorachio_rain_lifetime_mm:" not in text:
        violations.append("Missing input_number.nedorachio_rain_lifetime_mm")
    if "nedorachio_rain_week_baseline_mm:" not in text:
        violations.append("Missing input_number.nedorachio_rain_week_baseline_mm")
    if "sensor.nedorachio_rain_observed_week" not in text:
        violations.append("Weather feeder must read sensor.nedorachio_rain_observed_week")
    if "rain_mm_last_48h" in text or "rain_observed_48h" in text:
        violations.append("Rain tracking must use calendar week, not 48h rolling")
    if "rain_mm_this_week" not in _read(FIRMWARE_COMPONENT):
        violations.append("Firmware must expose rain_mm_this_week number entity")
    if "rain_credit_mm_per_step_sensor" not in fw:
        violations.append("Firmware must expose rain_credit_mm_per_step sensor")
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
        check_ha_weekly_gallons_tracking,
        check_ha_gallons_tracking,
        check_ha_rain_week_wiring,
    ]
    violations: list[str] = []
    for check in checks:
        violations.extend(check())
    return violations
