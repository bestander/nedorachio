from nedorachio.gates import PreflightContext, evaluate_preflight
from nedorachio.models import OperationalConfig


def test_preflight_blocks_low_static_pressure():
    cfg = OperationalConfig(pressure_static_min_psi=30.0, pressure_static_max_psi=80.0)
    result = evaluate_preflight(
        cfg,
        PreflightContext(
            now_epoch=1000,
            rain_sensor_last_wet_epoch=0,
            rain_forecast_last_high_epoch=0,
            any_alarm_latched=False,
            static_pressure_psi=20.0,
        ),
        is_schedule=True,
    )
    assert not result.passed
    assert result.reason == "pressure_too_low"
    assert result.benign


def test_preflight_blocks_latched_alarm():
    cfg = OperationalConfig()
    result = evaluate_preflight(
        cfg,
        PreflightContext(
            now_epoch=1000,
            rain_sensor_last_wet_epoch=0,
            rain_forecast_last_high_epoch=0,
            any_alarm_latched=True,
            static_pressure_psi=50.0,
        ),
        is_schedule=True,
    )
    assert not result.passed
    assert result.reason == "alarm_latched"


def test_preflight_blocks_schedule_when_master_schedule_off():
    cfg = OperationalConfig(fallback_schedule_enabled=False)
    result = evaluate_preflight(
        cfg,
        PreflightContext(
            now_epoch=1000,
            rain_sensor_last_wet_epoch=0,
            rain_forecast_last_high_epoch=0,
            any_alarm_latched=False,
            static_pressure_psi=50.0,
        ),
        is_schedule=True,
    )
    assert not result.passed
    assert result.reason == "schedule_disabled"


def test_preflight_allows_manual_when_master_schedule_off():
    cfg = OperationalConfig(fallback_schedule_enabled=False)
    result = evaluate_preflight(
        cfg,
        PreflightContext(
            now_epoch=1000,
            rain_sensor_last_wet_epoch=0,
            rain_forecast_last_high_epoch=0,
            any_alarm_latched=False,
            static_pressure_psi=50.0,
        ),
        is_schedule=False,
    )
    assert result.passed
