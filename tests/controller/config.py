"""Firmware-default tunables for simulation (from 04-tunables.yaml)."""

from __future__ import annotations

from dataclasses import dataclass, field


FALLBACK_START_EPOCH = 1780329600  # 2026-06-01 11:00 EST


@dataclass
class ZoneConfig:
    total_min: float = 20.0
    cycle_min: float = 10.0
    soak_min: float = 15.0
    min_interval_hours: float = 48.0
    min_flow_gpm: float = 1.0
    max_flow_gpm: float = 20.0
    start_minimum_psi: float = 35.0
    start_maximum_psi: float = 85.0
    minimum_running_psi: float = 20.0
    minimum_running_psi_grace_seconds: int = 5
    schedule_mode: int = 0  # 0=minutes, 1=gallons
    goal_gallons: float = 0.0
    cycle_gallons: float = 0.0


@dataclass
class ControllerConfig:
    zones: list[ZoneConfig] = field(default_factory=lambda: [ZoneConfig() for _ in range(8)])
    zones_enabled_bitmask: int = 0b1111  # zones 1-4
    fallback_schedule_enabled: bool = True
    master_enable: bool = True
    emergency_stop: bool = False
    time_synced: bool = True

    schedule_start_hour: int = 0
    schedule_start_minute: int = 0
    schedule_end_hour: int = 8
    schedule_end_minute: int = 0
    blackout_weekday_bitmask: int = 0
    maximum_runtime_minutes: float = 60.0
    attempt_cooldown_minutes: float = 20.0
    inter_zone_delay_s: float = 2.0

    pressure_static_min_psi: float = 30.0
    pressure_static_max_psi: float = 80.0
    pressure_running_min_psi: float = 25.0
    pressure_high_psi: float = 90.0

    pulses_per_gallon: float = 344.4
    phantom_flow_gpm: float = 0.5
    no_flow_grace_s: float = 60.0
    high_flow_grace_s: float = 30.0

    gate_rain_sensor: bool = True
    gate_static_pressure_preflight: bool = True
    gate_alarm_phantom_flow: bool = True
    gate_alarm_no_flow: bool = True
    gate_alarm_high_flow: bool = True
    gate_alarm_low_pressure: bool = True
    gate_alarm_high_pressure: bool = True
    high_pressure_cancels_run: bool = False

    rain_sensor_wet: bool = False
    rain_mm_last_48h: float = 0.0
    rain_mm_last_pushed_epoch: int = 0
    rain_mm_threshold_48h: float = 6.0
    rain_hold_hours_after_sensor: float = 24.0
    rain_hold_hours_after_forecast: float = 24.0
    rain_mm_max_age_hours: float = 12.0

    fallback_start_epoch: int = FALLBACK_START_EPOCH

    def zone_enabled(self, zone_id: int) -> bool:
        return bool((self.zones_enabled_bitmask >> (zone_id - 1)) & 1)

    def zone(self, zone_id: int) -> ZoneConfig:
        return self.zones[zone_id - 1]
