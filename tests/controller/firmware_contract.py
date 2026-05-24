"""
Static checks on firmware YAML — catches ESP-specific anti-patterns in remaining packages.

Legacy lambda engine packages (05-engine, 06-schedule, …) were removed; schedule logic
lives in the C++ nedorachio component and is tested via the Python canonical library.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FIRMWARE = REPO_ROOT / "firmware" / "packages"
FIRMWARE_COMPONENT_CPP = REPO_ROOT / "firmware" / "components" / "nedorachio"


def _read(name: str) -> str:
    return (FIRMWARE / name).read_text(encoding="utf-8")


def check_no_nvs_persistence() -> list[str]:
    """Device state is RAM-only; HA owns persisted cadence and config."""
    violations: list[str] = []
    for path in sorted(FIRMWARE.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        if "restore_value: true" in text:
            violations.append(f"{path.name}: restore_value must be false (no NVS globals)")
        if re.search(r"restore_mode:\s*RESTORE_", text):
            violations.append(f"{path.name}: switch restore_mode must be DISABLED (no NVS)")
    return violations


def check_no_blocking_delay_in_lambda_while() -> list[str]:
    """Remaining calibration lambdas must not block the main loop."""
    violations: list[str] = []
    for path in sorted(FIRMWARE.glob("*.yaml")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"lambda:\s*\|-\n((?:          .*\n?)*)", text):
            body = match.group(1)
            lines = [ln[10:] if ln.startswith("          ") else ln for ln in body.splitlines()]
            joined = "\n".join(lines)
            if re.search(r"\bwhile\s*\(", joined) and re.search(r"\bdelay\s*\(", joined):
                violations.append(
                    f"{path.name}: lambda combines while() and delay() (blocks ESP32 main loop)"
                )
    return violations


def check_component_package_present() -> list[str]:
    if not (FIRMWARE / "10-nedorachio-component.yaml").is_file():
        return ["Missing firmware/packages/10-nedorachio-component.yaml"]
    return []


def check_config_profile_package_present() -> list[str]:
    path = FIRMWARE / "11-config-profile.yaml"
    if not path.is_file():
        return ["Missing firmware/packages/11-config-profile.yaml"]
    text = path.read_text(encoding="utf-8")
    if "config_profile:" not in text:
        return ["11-config-profile.yaml must define nedorachio.config_profile"]
    return []


def check_fallback_schedule_switch_reflects_engine() -> list[str]:
    text = _read("10-nedorachio-component.yaml")
    violations: list[str] = []
    if "id: fallback_schedule_enabled" not in text:
        violations.append("Missing fallback_schedule_enabled switch")
        return violations
    block = text.split("id: fallback_schedule_enabled", 1)[1].split("\n  - platform:", 1)[0]
    if "optimistic: false" not in block:
        violations.append("fallback_schedule_enabled must use optimistic: false")
    if "fallback_schedule_enabled();" not in block:
        violations.append("fallback_schedule_enabled must lambda-read id(irrigation).fallback_schedule_enabled()")
    if "restore_mode: DISABLED" not in block:
        violations.append("fallback_schedule_enabled must use restore_mode: DISABLED (no NVS)")
    return violations


def check_component_loads_config_profile_from_yaml() -> list[str]:
    """Config sync removed — profile is baked in at flash time via config_profile YAML field."""
    violations: list[str] = []
    cpp = (FIRMWARE_COMPONENT_CPP / "nedorachio_component.cpp").read_text(encoding="utf-8")
    header = (FIRMWARE_COMPONENT_CPP / "nedorachio_component.h").read_text(encoding="utf-8")
    init_py = (FIRMWARE_COMPONENT_CPP / "__init__.py").read_text(encoding="utf-8")

    if "apply_config_profile_" not in cpp:
        violations.append("nedorachio_component.cpp must apply config_profile at setup")
    if "on_config_chunk" in cpp or "on_config_chunk" in header:
        violations.append("component must not implement config chunk assembly")
    if "on_config_json" in cpp or "on_config_json" in header:
        violations.append("component must not accept runtime config JSON from HA text entity")
    if "set_config_profile" not in header:
        violations.append("nedorachio_component.h must expose set_config_profile")
    if "CONF_CONFIG_PROFILE" not in init_py:
        violations.append("__init__.py must define CONF_CONFIG_PROFILE")
    if "config_text_id" in init_py:
        violations.append("__init__.py must not reference config_text_id")
    if "cv.Required(CONF_CONFIG_PROFILE)" not in init_py:
        violations.append("__init__.py must require config_profile in schema")
    return violations


def all_firmware_contract_violations() -> list[str]:
    checks = [
        check_component_package_present,
        check_config_profile_package_present,
        check_fallback_schedule_switch_reflects_engine,
        check_component_loads_config_profile_from_yaml,
        check_no_nvs_persistence,
        check_no_blocking_delay_in_lambda_while,
    ]
    violations: list[str] = []
    for check in checks:
        violations.extend(check())
    return violations
