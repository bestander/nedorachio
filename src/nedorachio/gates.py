from __future__ import annotations

from dataclasses import dataclass

from nedorachio.models import OperationalConfig, PreflightResult


@dataclass
class PreflightContext:
    now_epoch: int
    rain_sensor_last_wet_epoch: int
    rain_forecast_last_high_epoch: int
    any_alarm_latched: bool
    static_pressure_psi: float | None = None


BENIGN_REASONS = frozenset(
    {
        "rain_sensor_wet",
        "rain_hold_after_sensor",
        "rain_forecast_high",
        "rain_forecast_hold",
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
            hold_s = int(config.rain_hold_hours_after_sensor * 3600)
            if (
                ctx.rain_sensor_last_wet_epoch > 0
                and ctx.now_epoch - ctx.rain_sensor_last_wet_epoch < hold_s
            ):
                reason = "rain_hold_after_sensor"

    if not reason:
        ttl_s = int(config.rain_mm_max_age_hours * 3600)
        effective_mm = config.rain_mm_last_48h
        if config.rain_mm_last_pushed_epoch == 0 or ctx.now_epoch - config.rain_mm_last_pushed_epoch > ttl_s:
            effective_mm = 0.0
        if effective_mm > config.rain_mm_threshold_48h:
            reason = "rain_forecast_high"
        else:
            hold_s = int(config.rain_hold_hours_after_forecast * 3600)
            if (
                ctx.rain_forecast_last_high_epoch > 0
                and ctx.now_epoch - ctx.rain_forecast_last_high_epoch < hold_s
            ):
                reason = "rain_forecast_hold"

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


def rain_hold_active(
    config: OperationalConfig,
    *,
    now_epoch: int,
    rain_sensor_last_wet_epoch: int,
    rain_forecast_last_high_epoch: int,
) -> tuple[bool, str | None]:
    if config.gate_rain_sensor and config.rain_sensor_wet:
        return True, "rain_sensor_wet"
    hold_s = int(config.rain_hold_hours_after_sensor * 3600)
    if (
        rain_sensor_last_wet_epoch > 0
        and now_epoch - rain_sensor_last_wet_epoch < hold_s
    ):
        return True, "rain_hold_after_sensor"

    ttl_s = int(config.rain_mm_max_age_hours * 3600)
    effective_mm = config.rain_mm_last_48h
    if config.rain_mm_last_pushed_epoch == 0 or now_epoch - config.rain_mm_last_pushed_epoch > ttl_s:
        effective_mm = 0.0
    if effective_mm > config.rain_mm_threshold_48h:
        return True, "rain_forecast_high"
    hold_s = int(config.rain_hold_hours_after_forecast * 3600)
    if rain_forecast_last_high_epoch > 0 and now_epoch - rain_forecast_last_high_epoch < hold_s:
        return True, "rain_forecast_hold"
    return False, None
