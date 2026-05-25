from __future__ import annotations

import enum
from dataclasses import dataclass, field


@dataclass(frozen=True)
class WateringWindow:
    start: str
    end: str
    timezone: str


@dataclass(frozen=True)
class GlobalConfig:
    watering_window: WateringWindow
    rain_credit_mm_per_step: float
    rain_credit_gallons_per_zone_per_step: float
    rain_sensor_hold_hours_after_wet: float
    attempt_cooldown_minutes: float
    max_attempt_minutes: float
    no_flow_grace_seconds: float
    no_flow_sustain_seconds: float
    blackout_weekdays: tuple[str, ...]


@dataclass(frozen=True)
class ZoneConfigProfile:
    zone_id: int
    enabled: bool
    weekly_goal_gallons: float
    start_minimum_psi: float
    start_maximum_psi: float
    minimum_running_psi: float
    minimum_running_psi_grace_seconds: float
    minimum_flow_gpm: float
    maximum_flow_gpm: float


@dataclass(frozen=True)
class ConfigProfile:
    version: int
    global_: GlobalConfig
    zones: dict[int, ZoneConfigProfile]


@dataclass(frozen=True)
class ZonePlan:
    zone_id: int
    next_eligible_epoch: int | None
    blocked_reason: str | None
    weekly_delivered_gallons: float
    weekly_remaining_gallons: float
    weekly_goal_met: bool
    last_finished_epoch: int


@dataclass
class WateringSchedule:
    computed_at_epoch: int
    zones: dict[int, ZonePlan]
    next_action_epoch: int | None = None
    next_action: str = "idle"


@dataclass(frozen=True)
class RelayCommand:
    zone_id: int
    desired_on: bool
    reason: str


class EventType(enum.Enum):
    PREFLIGHT_FAIL = "preflight_fail"
    PREFLIGHT_SKIP = "preflight_skip"
    SCHEDULE_FIRE = "schedule_fire"
    ZONE_ON = "zone_on"
    ZONE_OFF = "zone_off"
    RUN_COMPLETE = "run_complete"
    RUN_CANCEL = "run_cancel"
    PLAN_UPDATED = "plan_updated"
    ALARM = "alarm"


@dataclass
class SimEvent:
    at_epoch: int
    kind: EventType
    detail: str = ""
    zone_id: int = 0


@dataclass(frozen=True)
class BackgroundActivity:
    ticks: int
    safety_ticks: int
    eval_ticks: int
    plan_ticks: int
    ticks_while_zone_on: int


@dataclass
class ZoneRuntimeState:
    last_finished_epoch: int = 0
    scheduled_next_epoch: int = 0
    actual_state: bool = False
    weekly_delivered_shadow: float = 0.0
    ha_weekly_delivered: float = 0.0
    last_attempt_epoch: int = 0


@dataclass
class RunState:
    zone_id: int = 0
    goal_gallons: float = 0.0
    gallons_done: float = 0.0
    run_start_delivered: float = 0.0
    cancel_requested: bool = False
    cancel_cause: str = ""
    started_pulses: int = 0
    started_ms: int = 0


@dataclass
class ZoneRuntime:
    last_finished_epoch: int = 0
    weekly_delivered_shadow: float = 0.0
    last_attempt_epoch: int = 0


@dataclass
class PreflightResult:
    passed: bool
    reason: str = ""
    benign: bool = False


@dataclass
class OperationalZoneConfig:
    weekly_goal_gallons: float = 0.0
    min_flow_gpm: float = 1.0
    max_flow_gpm: float = 20.0
    start_minimum_psi: float = 35.0
    start_maximum_psi: float = 85.0
    minimum_running_psi: float = 20.0
    minimum_running_psi_grace_seconds: int = 5


FALLBACK_START_EPOCH = 1780329600  # 2026-06-01 11:00 EST
HA_WEEKLY_STALENESS_SECONDS = 900


@dataclass
class OperationalConfig:
    """Runtime tunables used by the engine (simulation and device)."""

    zones: list[OperationalZoneConfig] = field(
        default_factory=lambda: [OperationalZoneConfig() for _ in range(8)]
    )
    zones_enabled_bitmask: int = 0b1111
    fallback_schedule_enabled: bool = True
    master_enable: bool = True
    emergency_stop: bool = False
    time_synced: bool = True

    schedule_start_hour: int = 0
    schedule_start_minute: int = 0
    schedule_end_hour: int = 8
    schedule_end_minute: int = 0
    blackout_weekday_bitmask: int = 0
    max_attempt_minutes: float = 30.0
    attempt_cooldown_minutes: float = 20.0
    inter_zone_delay_s: float = 2.0

    week_id_shadow: int = 0
    last_served_zone_id: int = 0
    tracking_source: str = "local"
    ha_weekly_feed_valid: bool = False
    ha_weekly_staleness_seconds: int = HA_WEEKLY_STALENESS_SECONDS

    pressure_static_min_psi: float = 30.0
    pressure_static_max_psi: float = 80.0
    pressure_running_min_psi: float = 25.0
    pressure_high_psi: float = 90.0

    pulses_per_gallon: float = 344.4
    phantom_flow_gpm: float = 0.5
    no_flow_grace_s: float = 60.0
    no_flow_sustain_s: float = 30.0
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
    rain_mm_this_week: float = 0.0
    rain_mm_last_pushed_epoch: int = 0
    rain_credit_mm_per_step: float = 10.0
    rain_credit_gallons_per_zone_per_step: float = 100.0
    rain_sensor_hold_hours_after_wet: float = 24.0
    rain_mm_max_age_hours: float = 12.0

    fallback_start_epoch: int = FALLBACK_START_EPOCH

    def zone_enabled(self, zone_id: int) -> bool:
        return bool((self.zones_enabled_bitmask >> (zone_id - 1)) & 1)

    def zone(self, zone_id: int) -> OperationalZoneConfig:
        return self.zones[zone_id - 1]
