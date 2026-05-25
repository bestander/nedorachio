from __future__ import annotations

from dataclasses import dataclass

from nedorachio.models import OperationalConfig, PreflightResult


@dataclass
class PreflightContext:
    now_epoch: int
    rain_sensor_last_wet_epoch: int
    any_alarm_latched: bool
    static_pressure_psi: float | None = None


BENIGN_REASONS = frozenset(
    {
        "rain_sensor_wet",
        "rain_hold_after_sensor",
        "pressure_too_low",
        "pressure_too_high",
        "alarm_latched",
    }
)


def evaluate_preflight(
    config: OperationalConfig,
    ctx: PreflightContext,
    *,
    is_schedule: bool,
) -> PreflightResult:
    reason = ""

    if not config.master_enable:
        reason = "master_enable_off"
    elif config.emergency_stop:
        reason = "emergency_stop_latched"
    elif not config.time_synced:
        reason = "time_not_synced"
    elif config.gate_rain_sensor:
        if config.rain_sensor_wet:
            reason = "rain_sensor_wet"
        else:
            hold_s = int(config.rain_sensor_hold_hours_after_wet * 3600)
            if (
                ctx.rain_sensor_last_wet_epoch > 0
                and ctx.now_epoch - ctx.rain_sensor_last_wet_epoch < hold_s
            ):
                reason = "rain_hold_after_sensor"

    if not reason and ctx.static_pressure_psi is not None and config.gate_static_pressure_preflight:
        if ctx.static_pressure_psi < config.pressure_static_min_psi:
            reason = "pressure_too_low"
        elif ctx.static_pressure_psi > config.pressure_static_max_psi:
            reason = "pressure_too_high"

    if not reason and is_schedule and not config.fallback_schedule_enabled:
        reason = "schedule_disabled"

    if not reason and ctx.any_alarm_latched:
        reason = "alarm_latched"

    if reason:
        return PreflightResult(passed=False, reason=reason, benign=reason in BENIGN_REASONS)
    return PreflightResult(passed=True)
