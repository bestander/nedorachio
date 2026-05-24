"""
Static checks on firmware YAML — catches bugs the Python simulator cannot model.

The simulator uses cooperative generators (`yield from _delay(1)`), so it never
reproduces ESPHome anti-patterns like `delay()` inside a C++ lambda while-loop
(that starves the ESP32 main loop and causes reboots ~20–30s into a run).

These contracts read the real firmware sources and fail CI when forbidden patterns
return or required recovery wiring is removed.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FIRMWARE = REPO_ROOT / "firmware" / "packages"


def _read(name: str) -> str:
    return (FIRMWARE / name).read_text(encoding="utf-8")


def _extract_script_block(yaml_text: str, script_id: str) -> str:
    """Return the YAML body of `- id: <script_id>` through the next `- id:` script."""
    pattern = rf"- id: {re.escape(script_id)}\n(.*?)(?=\n  - id: |\nscript:|\nbinary_sensor:|\nbutton:|\ninterval:|\nglobals:|\Z)"
    match = re.search(pattern, yaml_text, re.DOTALL)
    if not match:
        raise AssertionError(f"Script {script_id!r} not found in firmware YAML")
    return match.group(1)


def _lambda_bodies(block: str) -> list[str]:
    bodies: list[str] = []
    for match in re.finditer(r"lambda:\s*\|-\n((?:          .*\n?)*)", block):
        raw = match.group(1)
        lines = [ln[10:] if ln.startswith("          ") else ln for ln in raw.splitlines()]
        bodies.append("\n".join(lines))
    for match in re.finditer(r"lambda:\s*'([^']+)'", block):
        bodies.append(match.group(1))
    return bodies


def check_gallons_target_has_no_blocking_delay_loop() -> list[str]:
    """
    `run_one_zone_gallons_target` must not use delay() inside a C++ while in lambda.

    Use script `while:` + `delay: 1s` steps instead (see run_one_zone minutes mode).
    """
    engine = _read("05-engine.yaml")
    block = _extract_script_block(engine, "run_one_zone_gallons_target")
    violations: list[str] = []
    for i, body in enumerate(_lambda_bodies(block)):
        if re.search(r"\bwhile\s*\(", body) and re.search(r"\bdelay\s*\(", body):
            violations.append(
                f"run_one_zone_gallons_target lambda #{i + 1} combines while() and delay() "
                "(blocks ESP32 main loop — use script while + delay: 1s)"
            )
    return violations


def check_schedule_clears_recoverable_alarms_before_preflight() -> list[str]:
    schedule = _read("06-schedule.yaml")
    block = _extract_script_block(schedule, "schedule_fire_handler")
    if "clear_recoverable_alarms" not in block:
        return ["schedule_fire_handler must call clear_recoverable_alarms before pre-flight"]
    pre_idx = block.find("run_pre_flight")
    clear_idx = block.find("clear_recoverable_alarms")
    if clear_idx < 0 or pre_idx < 0 or clear_idx > pre_idx:
        return ["clear_recoverable_alarms must run before run_pre_flight in schedule_fire_handler"]
    return []


def check_recoverable_cancels_do_not_latch() -> list[str]:
    """Only phantom flow may set any_alarm_latched from mid-run / safety checks."""
    engine = _read("05-engine.yaml")
    violations: list[str] = []

    # Pre-flight must not latch (recoverable skips retry via cooldown).
    preflight = _extract_script_block(engine, "run_pre_flight")
    if "any_alarm_latched) = true" in preflight.replace(" ", ""):
        violations.append("run_pre_flight must not set any_alarm_latched")

    # Mid-run cancel block: count latch assignments; expect exactly one (phantom).
    interval_match = re.search(
        r"interval:\s*\n\s*- interval: 1s\s*\n\s*then:(.*?)(?=\n  - interval:|\Z)",
        engine,
        re.DOTALL,
    )
    if not interval_match:
        violations.append("Could not locate 1s safety interval in 05-engine.yaml")
        return violations

    safety = interval_match.group(1)
    latch_lines = [
        ln.strip()
        for ln in safety.splitlines()
        if "any_alarm_latched" in ln and "= true" in ln
    ]
    if len(latch_lines) != 1:
        violations.append(
            f"Expected exactly one any_alarm_latched = true in 1s safety interval "
            f"(phantom flow only), found {len(latch_lines)}: {latch_lines}"
        )
    elif "phantom" not in safety.lower() and "alarm_phantom_flow" not in safety:
        violations.append("The sole any_alarm_latched assignment should be for phantom flow")
    return violations


def check_clear_recoverable_alarms_script_exists() -> list[str]:
    engine = _read("05-engine.yaml")
    if "- id: clear_recoverable_alarms" not in engine:
        return ["Missing clear_recoverable_alarms script in 05-engine.yaml"]
    return []


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


def check_gallons_target_caps_final_chunk_at_goal() -> list[str]:
    """Final chunk must not deliver a full cycle_gallons past run_goal_gallons."""
    engine = _read("05-engine.yaml")
    block = _extract_script_block(engine, "run_one_zone_gallons_target")
    if "chunk_limit" not in block or "remaining" not in block:
        return ["run_one_zone_gallons_target must cap chunk size to remaining goal gallons"]
    if "run_base_gallons" not in block:
        return ["run_one_zone_gallons_target must use run_base_gallons for pulse accounting"]
    return []


def check_gallons_target_stamps_cadence_on_complete() -> list[str]:
    engine = _read("05-engine.yaml")
    block = _extract_script_block(engine, "run_one_zone_gallons_target")
    if "stamp_zone_last_finished" not in block:
        return ["run_one_zone_gallons_target must call stamp_zone_last_finished on successful completion"]
    return []


def check_plan_readout_frozen_while_running() -> list[str]:
    schedule = _read("06-schedule.yaml")
    plan_match = re.search(
        r"interval:\s*30s\s*\n\s*then:(.*?)(?=\n  - interval: 60s|\Z)",
        schedule,
        re.DOTALL,
    )
    if not plan_match:
        return ["Could not locate 30s plan interval in 06-schedule.yaml"]
    block = plan_match.group(1)
    zero_idx = block.find("zone_1_scheduled_next_epoch) = 0")
    run_idx = block.find("currently_on_zone")
    if zero_idx < 0 or run_idx < 0 or run_idx > zero_idx:
        return ["30s plan must return before clearing scheduled times when a zone is running"]
    return []


def all_firmware_contract_violations() -> list[str]:
    checks = [
        check_gallons_target_has_no_blocking_delay_loop,
        check_gallons_target_caps_final_chunk_at_goal,
        check_gallons_target_stamps_cadence_on_complete,
        check_plan_readout_frozen_while_running,
        check_clear_recoverable_alarms_script_exists,
        check_schedule_clears_recoverable_alarms_before_preflight,
        check_recoverable_cancels_do_not_latch,
        check_no_nvs_persistence,
    ]
    violations: list[str] = []
    for check in checks:
        violations.extend(check())
    return violations
