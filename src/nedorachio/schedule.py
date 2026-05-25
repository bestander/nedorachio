from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from nedorachio.models import OperationalConfig, ZonePlan, ZoneRuntime, ZoneRuntimeState


def weekday_bitmask_from_names(weekdays: tuple[str, ...]) -> int:
    idx = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
    mask = 0
    for day in weekdays:
        mask |= 1 << idx[day.lower()]
    return mask


def calendar_week_id(epoch: int, *, tz: ZoneInfo) -> int:
    """ISO year×100 + ISO week number (Monday-based week)."""
    if epoch <= 0:
        return 0
    dt = datetime.fromtimestamp(epoch, tz=tz)
    iso = dt.isocalendar()
    return iso.year * 100 + iso.week


def next_calendar_week_start_epoch(epoch: int, *, tz: ZoneInfo) -> int:
    """Next Monday 00:00 local time when the weekly gallon quota resets."""
    if epoch <= 0:
        return 0
    dt = datetime.fromtimestamp(epoch, tz=tz)
    midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    this_monday = midnight - timedelta(days=dt.weekday())
    if epoch <= int(this_monday.timestamp()):
        return int(this_monday.timestamp())
    return int((this_monday + timedelta(days=7)).timestamp())


def maybe_apply_week_reset(
    zones: list[ZoneRuntimeState],
    *,
    week_id_shadow: int,
    current_week_id: int,
) -> int:
    if current_week_id == 0:
        return week_id_shadow
    if week_id_shadow == 0:
        return current_week_id
    if week_id_shadow == current_week_id:
        return week_id_shadow
    for zs in zones:
        zs.weekly_delivered_shadow = 0.0
        zs.ha_weekly_delivered = 0.0
    return current_week_id


def weekly_delivered_effective(
    zone: ZoneRuntimeState,
    *,
    ha_feed_valid: bool,
) -> float:
    if ha_feed_valid:
        return max(0.0, zone.ha_weekly_delivered)
    return max(0.0, zone.weekly_delivered_shadow)


def accept_ha_weekly_update(current: float, incoming: float) -> bool:
    """Ignore stale HA reads (especially 0 on reconnect) that would lower progress."""
    incoming = max(0.0, incoming)
    return incoming + 1e-3 >= max(0.0, current)


def effective_rain_mm_this_week(
    config: OperationalConfig,
    *,
    now_epoch: int,
) -> float:
    ttl_s = int(config.rain_mm_max_age_hours * 3600)
    if (
        config.rain_mm_last_pushed_epoch == 0
        or now_epoch - config.rain_mm_last_pushed_epoch > ttl_s
    ):
        return 0.0
    return max(0.0, config.rain_mm_this_week)


def rain_credit_gallons_per_zone(
    rain_mm: float,
    *,
    mm_per_step: float,
    gallons_per_step: float,
) -> float:
    if mm_per_step <= 0:
        return 0.0
    return max(0.0, rain_mm * (gallons_per_step / mm_per_step))


def effective_weekly_goal(
    goal: float,
    rain_mm: float,
    *,
    mm_per_step: float,
    gallons_per_step: float,
) -> float:
    credit = rain_credit_gallons_per_zone(
        rain_mm,
        mm_per_step=mm_per_step,
        gallons_per_step=gallons_per_step,
    )
    return max(0.0, goal - credit)


def in_watering_window(
    *,
    hour: int,
    minute: int,
    start_hour: int,
    start_minute: int,
    end_hour: int,
    end_minute: int,
) -> bool:
    now_min = hour * 60 + minute
    start_min = start_hour * 60 + start_minute
    end_min = end_hour * 60 + end_minute
    if start_min == end_min:
        return False
    if start_min < end_min:
        return start_min <= now_min < end_min
    return now_min >= start_min or now_min < end_min


def is_blackout_day(*, dow_mon0: int, blackout_weekday_bitmask: int) -> bool:
    return bool((blackout_weekday_bitmask >> dow_mon0) & 1)


def next_schedule_opportunity_epoch(
    earliest_epoch: int,
    *,
    tz: ZoneInfo,
    start_hour: int,
    start_minute: int,
    end_hour: int,
    end_minute: int,
    blackout_weekday_bitmask: int,
) -> int:
    """Earliest epoch >= earliest_epoch when scheduled runs may start."""
    if earliest_epoch <= 0:
        return 0
    t = earliest_epoch
    for _ in range(14):
        dt = datetime.fromtimestamp(t, tz=tz)
        dow = dt.weekday()
        if is_blackout_day(dow_mon0=dow, blackout_weekday_bitmask=blackout_weekday_bitmask):
            midnight_next = (dt + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            t = int(midnight_next.timestamp())
            continue
        if in_watering_window(
            hour=dt.hour,
            minute=dt.minute,
            start_hour=start_hour,
            start_minute=start_minute,
            end_hour=end_hour,
            end_minute=end_minute,
        ):
            return t
        now_min = dt.hour * 60 + dt.minute
        start_min = start_hour * 60 + start_minute
        end_min = end_hour * 60 + end_minute
        if start_min < end_min:
            if now_min < start_min:
                candidate = dt.replace(
                    hour=start_hour, minute=start_minute, second=0, microsecond=0
                )
            else:
                candidate = (dt + timedelta(days=1)).replace(
                    hour=start_hour, minute=start_minute, second=0, microsecond=0
                )
        else:
            candidate = dt.replace(
                hour=start_hour, minute=start_minute, second=0, microsecond=0
            )
            if int(candidate.timestamp()) < t:
                candidate += timedelta(days=1)
        t = max(t, int(candidate.timestamp()))
    return t


def zone_has_weekly_deficit(
    config: OperationalConfig,
    zone: ZoneRuntimeState,
    zone_id: int,
    *,
    ha_feed_valid: bool,
    now_epoch: int,
) -> bool:
    goal = config.zone(zone_id).weekly_goal_gallons
    if goal <= 0:
        return False
    rain_mm = effective_rain_mm_this_week(config, now_epoch=now_epoch)
    target = effective_weekly_goal(
        goal,
        rain_mm,
        mm_per_step=config.rain_credit_mm_per_step,
        gallons_per_step=config.rain_credit_gallons_per_zone_per_step,
    )
    delivered = weekly_delivered_effective(zone, ha_feed_valid=ha_feed_valid)
    return delivered < target


def zone_cooldown_elapsed(
    zone: ZoneRuntimeState,
    now_epoch: int,
    cooldown_seconds: int,
) -> bool:
    if cooldown_seconds <= 0 or zone.last_attempt_epoch <= 0:
        return True
    return now_epoch >= zone.last_attempt_epoch + cooldown_seconds


def eligible_deficit_zones(
    config: OperationalConfig,
    zones: list[ZoneRuntimeState],
    now_epoch: int,
    *,
    ha_feed_valid: bool,
    respect_cooldown: bool,
) -> list[int]:
    cooldown_s = int(config.attempt_cooldown_minutes * 60)
    eligible: list[int] = []
    for zid in range(1, 9):
        if not config.zone_enabled(zid):
            continue
        zs = zones[zid - 1]
        if not zone_has_weekly_deficit(
            config, zs, zid, ha_feed_valid=ha_feed_valid, now_epoch=now_epoch
        ):
            continue
        if respect_cooldown and not zone_cooldown_elapsed(zs, now_epoch, cooldown_s):
            continue
        eligible.append(zid)
    return eligible


def pick_next_zone_round_robin(
    config: OperationalConfig,
    zones: list[ZoneRuntimeState],
    now_epoch: int,
    *,
    ha_feed_valid: bool,
    respect_cooldown: bool = True,
) -> int:
    eligible = eligible_deficit_zones(
        config,
        zones,
        now_epoch,
        ha_feed_valid=ha_feed_valid,
        respect_cooldown=respect_cooldown,
    )
    if not eligible:
        return 0

    start = config.last_served_zone_id
    for offset in range(1, 9):
        zid = ((start + offset - 1) % 8) + 1
        if zid in eligible:
            return zid
    return 0


def compute_zone_plans(
    config: OperationalConfig,
    zones: list[ZoneRuntimeState],
    *,
    now_epoch: int,
    tz: ZoneInfo,
    rain_blocked: bool = False,
    rain_blocked_reason: str | None = None,
    ha_time_valid: bool = True,
    ha_feed_valid: bool = False,
) -> dict[int, ZonePlan]:
    if not config.fallback_schedule_enabled or now_epoch == 0 or not ha_time_valid:
        return {}

    cooldown_s = int(config.attempt_cooldown_minutes * 60)
    plans: dict[int, ZonePlan] = {}

    rain_mm = effective_rain_mm_this_week(config, now_epoch=now_epoch)

    for zid in range(1, 9):
        if not config.zone_enabled(zid):
            continue
        zcfg = config.zone(zid)
        zs = zones[zid - 1]
        goal = zcfg.weekly_goal_gallons
        target = effective_weekly_goal(
            goal,
            rain_mm,
            mm_per_step=config.rain_credit_mm_per_step,
            gallons_per_step=config.rain_credit_gallons_per_zone_per_step,
        )
        delivered = weekly_delivered_effective(zs, ha_feed_valid=ha_feed_valid)
        remaining = max(0.0, target - delivered) if target > 0 else 0.0
        goal_met = goal > 0 and delivered >= target

        blocked_reason: str | None = None
        if goal_met:
            blocked_reason = "weekly_goal_met"
        elif rain_blocked:
            blocked_reason = rain_blocked_reason or "rain_hold"
        elif not zone_cooldown_elapsed(zs, now_epoch, cooldown_s):
            blocked_reason = "attempt_cooldown"

        next_eligible: int | None = None
        if goal_met:
            next_eligible = next_calendar_week_start_epoch(now_epoch, tz=tz)
        elif zs.last_attempt_epoch > 0 and cooldown_s > 0:
            next_eligible = next_schedule_opportunity_epoch(
                zs.last_attempt_epoch + cooldown_s,
                tz=tz,
                start_hour=config.schedule_start_hour,
                start_minute=config.schedule_start_minute,
                end_hour=config.schedule_end_hour,
                end_minute=config.schedule_end_minute,
                blackout_weekday_bitmask=config.blackout_weekday_bitmask,
            )
        elif blocked_reason is None:
            next_eligible = next_schedule_opportunity_epoch(
                now_epoch,
                tz=tz,
                start_hour=config.schedule_start_hour,
                start_minute=config.schedule_start_minute,
                end_hour=config.schedule_end_hour,
                end_minute=config.schedule_end_minute,
                blackout_weekday_bitmask=config.blackout_weekday_bitmask,
            )

        plans[zid] = ZonePlan(
            zone_id=zid,
            next_eligible_epoch=next_eligible,
            blocked_reason=blocked_reason,
            weekly_delivered_gallons=delivered,
            weekly_remaining_gallons=remaining,
            weekly_goal_met=goal_met,
            last_finished_epoch=zs.last_finished_epoch,
        )
    return plans


def update_scheduled_next_epochs(
    config: OperationalConfig,
    zones: list[ZoneRuntimeState],
    *,
    now_epoch: int,
    tz: ZoneInfo,
    ha_time_valid: bool = True,
    ha_feed_valid: bool = False,
) -> None:
    for zs in zones:
        zs.scheduled_next_epoch = 0
    if not config.fallback_schedule_enabled or now_epoch == 0 or not ha_time_valid:
        return

    plans = compute_zone_plans(
        config,
        zones,
        now_epoch=now_epoch,
        tz=tz,
        ha_time_valid=ha_time_valid,
        ha_feed_valid=ha_feed_valid,
    )
    for zid, plan in plans.items():
        zones[zid - 1].scheduled_next_epoch = plan.next_eligible_epoch or 0


def compute_watering_schedule(
    profile_global,
    zones_runtime: dict[int, ZoneRuntime],
    *,
    now_epoch: int,
    tz: ZoneInfo,
    zones_enabled: set[int],
    attempt_cooldown_minutes: float,
    max_attempt_minutes: float,
    start_hour: int,
    start_minute: int,
    end_hour: int,
    end_minute: int,
    blackout_weekdays: tuple[str, ...],
    fallback_start_epoch: int,
    rain_blocked: bool = False,
    ha_feed_valid: bool = False,
) -> dict[int, ZonePlan]:
    """High-level planner API for unit tests using ConfigProfile-shaped inputs."""
    blackout_mask = weekday_bitmask_from_names(blackout_weekdays)
    zone_states = [
        ZoneRuntimeState(
            last_finished_epoch=zones_runtime.get(zid, ZoneRuntime()).last_finished_epoch,
            weekly_delivered_shadow=zones_runtime.get(
                zid, ZoneRuntime()
            ).weekly_delivered_shadow,
            last_attempt_epoch=zones_runtime.get(zid, ZoneRuntime()).last_attempt_epoch,
        )
        for zid in range(1, 9)
    ]
    config = OperationalConfig(
        zones_enabled_bitmask=sum(1 << (z - 1) for z in zones_enabled),
        fallback_schedule_enabled=True,
        schedule_start_hour=start_hour,
        schedule_start_minute=start_minute,
        schedule_end_hour=end_hour,
        schedule_end_minute=end_minute,
        blackout_weekday_bitmask=blackout_mask,
        max_attempt_minutes=max_attempt_minutes,
        attempt_cooldown_minutes=attempt_cooldown_minutes,
        fallback_start_epoch=fallback_start_epoch,
        ha_weekly_feed_valid=ha_feed_valid,
    )
    for zid in zones_enabled:
        if zid in zones_runtime:
            config.zones[zid - 1].weekly_goal_gallons = 100.0
    return compute_zone_plans(
        config,
        zone_states,
        now_epoch=now_epoch,
        tz=tz,
        rain_blocked=rain_blocked,
        ha_feed_valid=ha_feed_valid,
    )
