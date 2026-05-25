from __future__ import annotations

import json
from pathlib import Path

from nedorachio.config import load_profile
from nedorachio.models import ConfigProfile, OperationalConfig, OperationalZoneConfig
from nedorachio.runtime_state import RuntimeState


def weekday_bitmask(weekdays: tuple[str, ...]) -> int:
    idx = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
    mask = 0
    for day in weekdays:
        mask |= 1 << idx[day.lower()]
    return mask


def parse_window_hour_minute(value: str) -> tuple[int, int]:
    parts = value.split(":")
    return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0


def operational_config_from_profile(profile: ConfigProfile, **overrides) -> OperationalConfig:
    """Build engine config from HA JSON profile."""
    g = profile.global_
    start_h, start_m = parse_window_hour_minute(g.watering_window.start)
    end_h, end_m = parse_window_hour_minute(g.watering_window.end)

    zones: list[OperationalZoneConfig] = []
    zone_mask = 0
    for zid in range(1, 9):
        zp = profile.zones.get(zid)
        if zp is None:
            zones.append(OperationalZoneConfig())
            continue
        if zp.enabled:
            zone_mask |= 1 << (zid - 1)
        zones.append(
            OperationalZoneConfig(
                weekly_goal_gallons=zp.weekly_goal_gallons,
                min_flow_gpm=zp.minimum_flow_gpm,
                max_flow_gpm=zp.maximum_flow_gpm,
                start_minimum_psi=zp.start_minimum_psi,
                start_maximum_psi=zp.start_maximum_psi,
                minimum_running_psi=zp.minimum_running_psi,
                minimum_running_psi_grace_seconds=int(zp.minimum_running_psi_grace_seconds),
            )
        )

    cfg = OperationalConfig(
        zones=zones,
        zones_enabled_bitmask=zone_mask,
        schedule_start_hour=start_h,
        schedule_start_minute=start_m,
        schedule_end_hour=end_h,
        schedule_end_minute=end_m,
        blackout_weekday_bitmask=weekday_bitmask(g.blackout_weekdays),
        attempt_cooldown_minutes=g.attempt_cooldown_minutes,
        max_attempt_minutes=g.max_attempt_minutes,
        no_flow_grace_s=g.no_flow_grace_seconds,
        no_flow_sustain_s=g.no_flow_sustain_seconds,
        rain_credit_mm_per_step=g.rain_credit_mm_per_step,
        rain_credit_gallons_per_zone_per_step=g.rain_credit_gallons_per_zone_per_step,
        rain_sensor_hold_hours_after_wet=g.rain_sensor_hold_hours_after_wet,
    )
    for key, value in overrides.items():
        if hasattr(cfg, key):
            setattr(cfg, key, value)
    return cfg


def apply_runtime_state_to_controller(sim, state: RuntimeState) -> None:
    """Load HA-persisted JSON into a running controller simulator."""
    sim.week_id_shadow = state.week_id_shadow
    sim.last_served_zone_id = state.last_served_zone_id
    for zid, rec in state.zones.items():
        if 1 <= zid <= 8:
            zs = sim.zones[zid - 1]
            if rec.last_finished_epoch > 0:
                zs.last_finished_epoch = rec.last_finished_epoch
            zs.weekly_delivered_shadow = rec.weekly_delivered_shadow
            zs.last_attempt_epoch = rec.last_attempt_epoch
    sim.rain_sensor_last_wet_epoch = state.rain_sensor_last_wet_epoch
    sim.rain_forecast_last_high_epoch = state.rain_forecast_last_high_epoch


def runtime_state_from_controller(sim, *, now_epoch: int) -> RuntimeState:
    """Snapshot controller weekly fields for HA persistence."""
    from nedorachio.runtime_state import RuntimeState, ZoneRuntimeRecord

    state = RuntimeState(
        updated_epoch=now_epoch,
        week_id_shadow=sim.week_id_shadow,
        last_served_zone_id=sim.last_served_zone_id,
    )
    for zid in range(1, 9):
        zs = sim.zones[zid - 1]
        state.zones[zid] = ZoneRuntimeRecord(
            last_finished_epoch=zs.last_finished_epoch,
            weekly_delivered_shadow=zs.weekly_delivered_shadow,
            last_attempt_epoch=zs.last_attempt_epoch,
        )
    state.rain_sensor_last_wet_epoch = sim.rain_sensor_last_wet_epoch
    state.rain_forecast_last_high_epoch = sim.rain_forecast_last_high_epoch
    return state


REPO_FIRMWARE_CONFIG = Path("firmware/packages/11-config-profile.yaml")


def load_repo_profile_json() -> dict:
    """Extract the config profile JSON from firmware/packages/11-config-profile.yaml."""
    raw = REPO_FIRMWARE_CONFIG.read_text(encoding="utf-8")
    marker = "config_profile: |"
    start = raw.find(marker)
    if start < 0:
        raise ValueError("config_profile block not found in 11-config-profile.yaml")
    lines = raw[start:].splitlines()[1:]
    json_lines: list[str] = []
    depth = 0
    started = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if started and depth == 0:
                break
            continue
        if not started:
            if stripped.startswith("{"):
                started = True
            else:
                continue
        json_lines.append(stripped)
        depth += stripped.count("{") - stripped.count("}")
        if started and depth == 0:
            break
    if not json_lines:
        raise ValueError("config profile JSON not found in firmware YAML")
    return json.loads("\n".join(json_lines))


def load_repo_profile():
    return load_profile(load_repo_profile_json())


def load_operational_from_repo_profile(**overrides) -> OperationalConfig:
    return operational_config_from_profile(load_repo_profile(), **overrides)
