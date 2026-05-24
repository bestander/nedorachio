from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from nedorachio.models import OperationalConfig, ZonePlan, ZoneRuntime, ZoneRuntimeState


def weekday_bitmask_from_names(weekdays: tuple[str, ...]) -> int:
    idx = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
    mask = 0
    for day in weekdays:
        mask |= 1 << idx[day.lower()]
    return mask


def effective_last_finished(
    last_finished_epoch: int,
    now_epoch: int,
    fallback_start_epoch: int,
    *,
    ha_time_valid: bool,
) -> int:
    if last_finished_epoch:
        return last_finished_epoch
    if ha_time_valid and now_epoch:
        return now_epoch
    return fallback_start_epoch


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


def snap_next_start(
    earliest_epoch: int,
    *,
    tz: ZoneInfo,
    start_hour: int,
    start_minute: int,
    end_hour: int,
    end_minute: int,
    blackout_weekday_bitmask: int,
) -> int:
    start_min_cfg = start_hour * 60 + start_minute
    end_min_cfg = end_hour * 60 + end_minute
    if start_min_cfg == end_min_cfg:
        return 0

    t = (earliest_epoch // 60) * 60
    if t < earliest_epoch:
        t += 60

    for _ in range(20160):
        dt = datetime.fromtimestamp(t, tz=tz)
        now_min = dt.hour * 60 + dt.minute
        dow_mon0 = dt.weekday()
        if start_min_cfg < end_min_cfg:
            in_window = start_min_cfg <= now_min < end_min_cfg
        else:
            in_window = now_min >= start_min_cfg or now_min < end_min_cfg
        blackout = bool((blackout_weekday_bitmask >> dow_mon0) & 1)
        if in_window and not blackout:
            return t
        t += 60
    return 0


def next_due_zone(
    config: OperationalConfig,
    zones: list[ZoneRuntimeState],
    now_epoch: int,
    *,
    ha_time_valid: bool = True,
) -> int:
    for zid in range(1, 9):
        if not config.zone_enabled(zid):
            continue
        zcfg = config.zone(zid)
        last = effective_last_finished(
            zones[zid - 1].last_finished_epoch,
            now_epoch,
            config.fallback_start_epoch,
            ha_time_valid=ha_time_valid,
        )
        interval_s = int(zcfg.min_interval_hours * 3600)
        if now_epoch >= last + interval_s:
            return zid
    return 0


def cadence_due_epoch(
    *,
    last_finished_epoch: int,
    min_interval_hours: float,
    fallback_start_epoch: int,
) -> int:
    last = last_finished_epoch or fallback_start_epoch
    return last + int(min_interval_hours * 3600)


def compute_zone_plans(
    config: OperationalConfig,
    zones: list[ZoneRuntimeState],
    *,
    now_epoch: int,
    tz: ZoneInfo,
    rain_blocked: bool = False,
    rain_blocked_reason: str | None = None,
    ha_time_valid: bool = True,
) -> dict[int, ZonePlan]:
    if not config.fallback_schedule_enabled or now_epoch == 0 or not ha_time_valid:
        return {}

    ideals: list[tuple[int, int]] = []
    for zid in range(1, 9):
        if not config.zone_enabled(zid):
            continue
        zcfg = config.zone(zid)
        if zcfg.min_interval_hours <= 0:
            continue
        last = effective_last_finished(
            zones[zid - 1].last_finished_epoch,
            now_epoch,
            config.fallback_start_epoch,
            ha_time_valid=ha_time_valid,
        )
        raw_due = cadence_due_epoch(
            last_finished_epoch=last,
            min_interval_hours=zcfg.min_interval_hours,
            fallback_start_epoch=config.fallback_start_epoch,
        )
        ideal = snap_next_start(
            raw_due,
            tz=tz,
            start_hour=config.schedule_start_hour,
            start_minute=config.schedule_start_minute,
            end_hour=config.schedule_end_hour,
            end_minute=config.schedule_end_minute,
            blackout_weekday_bitmask=config.blackout_weekday_bitmask,
        )
        if ideal:
            ideals.append((ideal, zid))

    ideals.sort(key=lambda x: (x[0], x[1]))

    cursor = 0
    dur_s = max(60, int(config.maximum_runtime_minutes * 60))
    assigned: dict[int, int] = {}

    for ideal, zid in ideals:
        merged = (now_epoch // 60) * 60 if ideal <= now_epoch else ideal
        if cursor > merged:
            merged = cursor
        actual = snap_next_start(
            merged,
            tz=tz,
            start_hour=config.schedule_start_hour,
            start_minute=config.schedule_start_minute,
            end_hour=config.schedule_end_hour,
            end_minute=config.schedule_end_minute,
            blackout_weekday_bitmask=config.blackout_weekday_bitmask,
        )
        if not actual:
            continue
        assigned[zid] = actual
        next_cursor = actual + dur_s
        cursor = snap_next_start(
            next_cursor,
            tz=tz,
            start_hour=config.schedule_start_hour,
            start_minute=config.schedule_start_minute,
            end_hour=config.schedule_end_hour,
            end_minute=config.schedule_end_minute,
            blackout_weekday_bitmask=config.blackout_weekday_bitmask,
        ) or next_cursor

    plans: dict[int, ZonePlan] = {}
    for zid in range(1, 9):
        if not config.zone_enabled(zid):
            continue
        zcfg = config.zone(zid)
        zs = zones[zid - 1]
        last = effective_last_finished(
            zs.last_finished_epoch,
            now_epoch,
            config.fallback_start_epoch,
            ha_time_valid=ha_time_valid,
        )
        raw_due = cadence_due_epoch(
            last_finished_epoch=last,
            min_interval_hours=zcfg.min_interval_hours,
            fallback_start_epoch=config.fallback_start_epoch,
        )
        blocked_reason: str | None = None
        if now_epoch < raw_due:
            blocked_reason = "not_due"
        elif rain_blocked:
            blocked_reason = rain_blocked_reason or "rain_hold"
        next_start = assigned.get(zid)
        if next_start is None and blocked_reason is None:
            blocked_reason = "unschedulable"
        goal = zcfg.goal_gallons if zcfg.schedule_mode == 1 else 0.0
        delivered = zs.cycle_delivered_gallons
        remaining = max(0.0, goal - delivered) if goal > 0 else 0.0
        plans[zid] = ZonePlan(
            zone_id=zid,
            next_start_epoch=next_start,
            blocked_reason=blocked_reason,
            cycle_delivered_gallons=delivered,
            cycle_remaining_gallons=remaining,
            last_finished_epoch=last,
        )
    return plans


def update_scheduled_next_epochs(
    config: OperationalConfig,
    zones: list[ZoneRuntimeState],
    *,
    now_epoch: int,
    tz: ZoneInfo,
    ha_time_valid: bool = True,
) -> None:
    for zs in zones:
        zs.scheduled_next_epoch = 0
    if not config.fallback_schedule_enabled or now_epoch == 0 or not ha_time_valid:
        return

    plans = compute_zone_plans(
        config, zones, now_epoch=now_epoch, tz=tz, ha_time_valid=ha_time_valid
    )
    for zid, plan in plans.items():
        zones[zid - 1].scheduled_next_epoch = plan.next_start_epoch or 0


def compute_watering_schedule(
    profile_global,
    zones_runtime: dict[int, ZoneRuntime],
    *,
    now_epoch: int,
    tz: ZoneInfo,
    zones_enabled: set[int],
    min_interval_hours: float,
    maximum_runtime_minutes: float,
    start_hour: int,
    start_minute: int,
    end_hour: int,
    end_minute: int,
    blackout_weekdays: tuple[str, ...],
    fallback_start_epoch: int,
    rain_blocked: bool = False,
) -> dict[int, ZonePlan]:
    """High-level planner API for unit tests using ConfigProfile-shaped inputs."""
    blackout_mask = weekday_bitmask_from_names(blackout_weekdays)
    zone_states = [
        ZoneRuntimeState(
            last_finished_epoch=zones_runtime.get(zid, ZoneRuntime()).last_finished_epoch,
            cycle_delivered_gallons=zones_runtime.get(
                zid, ZoneRuntime()
            ).cycle_delivered_gallons,
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
        maximum_runtime_minutes=maximum_runtime_minutes,
        fallback_start_epoch=fallback_start_epoch,
    )
    for zid in zones_enabled:
        config.zones[zid - 1].min_interval_hours = min_interval_hours
    return compute_zone_plans(
        config,
        zone_states,
        now_epoch=now_epoch,
        tz=tz,
        rain_blocked=rain_blocked,
    )
