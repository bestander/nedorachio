from __future__ import annotations

from nedorachio.models import ConfigProfile, GlobalConfig, WateringWindow, ZoneConfigProfile

WEEKDAY_ALIASES = frozenset({"mon", "tue", "wed", "thu", "fri", "sat", "sun"})


def load_profile(data: dict) -> ConfigProfile:
    g = data["global"]
    ww = g["watering_window"]
    blackout = tuple(d.lower() for d in g.get("blackout", {}).get("weekdays", []))
    for day in blackout:
        if day not in WEEKDAY_ALIASES:
            raise ValueError(f"invalid blackout weekday: {day}")

    max_attempt = g.get("max_attempt_minutes", g.get("maximum_runtime_minutes"))
    if max_attempt is None:
        raise ValueError("global.max_attempt_minutes is required")

    rain_mm = g.get("rain_credit_mm_per_step")
    rain_gal = g.get("rain_credit_gallons_per_zone_per_step")
    if rain_mm is None or rain_gal is None:
        raise ValueError(
            "global.rain_credit_mm_per_step and rain_credit_gallons_per_zone_per_step are required"
        )

    global_cfg = GlobalConfig(
        watering_window=WateringWindow(
            start=ww["start"],
            end=ww["end"],
            timezone=ww["timezone"],
        ),
        rain_credit_mm_per_step=float(rain_mm),
        rain_credit_gallons_per_zone_per_step=float(rain_gal),
        rain_sensor_hold_hours_after_wet=float(
            g.get("rain_sensor_hold_hours_after_wet", 24)
        ),
        attempt_cooldown_minutes=float(g["attempt_cooldown_minutes"]),
        max_attempt_minutes=float(max_attempt),
        no_flow_grace_seconds=float(g["no_flow_grace_seconds"]),
        no_flow_sustain_seconds=float(g["no_flow_sustain_seconds"]),
        blackout_weekdays=blackout,
    )

    zones: dict[int, ZoneConfigProfile] = {}
    for key, z in data["zones"].items():
        zone_id = int(key)
        weekly_goal = z.get("weekly_goal_gallons", z.get("goal_gallons_per_cycle"))
        if weekly_goal is None:
            raise ValueError(f"zones.{key}.weekly_goal_gallons is required")
        zones[zone_id] = ZoneConfigProfile(
            zone_id=zone_id,
            enabled=bool(z["enabled"]),
            weekly_goal_gallons=float(weekly_goal),
            start_minimum_psi=float(z["start_minimum_psi"]),
            start_maximum_psi=float(z["start_maximum_psi"]),
            minimum_running_psi=float(z["minimum_running_psi"]),
            minimum_running_psi_grace_seconds=float(z["minimum_running_psi_grace_seconds"]),
            minimum_flow_gpm=float(z["minimum_flow_gpm"]),
            maximum_flow_gpm=float(z["maximum_flow_gpm"]),
        )

    return ConfigProfile(version=int(data["version"]), global_=global_cfg, zones=zones)


def profile_to_operational(profile: ConfigProfile) -> tuple[dict, int, int]:
    """Map profile JSON to operational engine fields. Returns (zone_overrides, bitmask, blackout_mask)."""
    weekday_idx = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
    blackout_mask = 0
    for day in profile.global_.blackout_weekdays:
        blackout_mask |= 1 << weekday_idx[day]

    zone_mask = 0
    for zone_id, z in profile.zones.items():
        if z.enabled:
            zone_mask |= 1 << (zone_id - 1)

    return {}, zone_mask, blackout_mask
