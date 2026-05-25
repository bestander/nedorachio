#include "schedule.h"

#include <algorithm>
#include <cstdlib>
#include <ctime>

namespace esphome {
namespace nedorachio {

namespace {

void local_tm_from_epoch(uint32_t epoch, bool ha_time_valid, struct tm &lt) {
  time_t tt = static_cast<time_t>(epoch);
  if (ha_time_valid) {
    localtime_r(&tt, &lt);
  } else {
    time_t est_epoch = tt - 5 * 3600;
    gmtime_r(&est_epoch, &lt);
  }
}

uint32_t next_calendar_week_start_epoch(uint32_t epoch, bool ha_time_valid) {
  if (epoch == 0)
    return 0;
  struct tm lt {};
  local_tm_from_epoch(epoch, ha_time_valid, lt);
  struct tm mon = lt;
  mon.tm_hour = 0;
  mon.tm_min = 0;
  mon.tm_sec = 0;
  const int dow = (lt.tm_wday + 6) % 7;
  mon.tm_mday -= dow;
  time_t this_monday = mktime(&mon);
  if (this_monday == -1)
    return 0;
  if (epoch <= (uint32_t) this_monday)
    return (uint32_t) this_monday;
  return (uint32_t) (this_monday + 7 * 86400);
}

}  // namespace

bool in_watering_window(int hour, int minute, const GlobalConfig &g) {
  const int now_min = hour * 60 + minute;
  const int start_min = g.schedule_start_hour * 60 + g.schedule_start_minute;
  const int end_min = g.schedule_end_hour * 60 + g.schedule_end_minute;
  if (start_min == end_min)
    return false;
  if (start_min < end_min)
    return start_min <= now_min && now_min < end_min;
  return now_min >= start_min || now_min < end_min;
}

bool is_blackout_day(int dow_mon0, int blackout_weekday_bitmask) {
  return ((blackout_weekday_bitmask >> dow_mon0) & 1) != 0;
}

int calendar_week_id(uint32_t epoch, bool ha_time_valid) {
  if (epoch == 0)
    return 0;
  struct tm lt {};
  local_tm_from_epoch(epoch, ha_time_valid, lt);
  char buf[8];
  strftime(buf, sizeof(buf), "%G", &lt);
  const int iso_year = atoi(buf);
  strftime(buf, sizeof(buf), "%V", &lt);
  const int iso_week = atoi(buf);
  return iso_year * 100 + iso_week;
}

int maybe_apply_week_reset(ZoneRuntime *zones, int week_id_shadow, int current_week_id) {
  if (current_week_id == 0)
    return week_id_shadow;
  if (week_id_shadow == 0)
    return current_week_id;
  if (week_id_shadow == current_week_id)
    return week_id_shadow;
  for (int i = 0; i < kNumZones; i++) {
    zones[i].weekly_delivered_shadow = 0.0f;
    zones[i].ha_weekly_delivered = 0.0f;
  }
  return current_week_id;
}

float weekly_delivered_effective(const ZoneRuntime &zone, bool ha_feed_valid) {
  if (ha_feed_valid)
    return std::max(0.0f, zone.ha_weekly_delivered);
  return std::max(0.0f, zone.weekly_delivered_shadow);
}

bool accept_ha_weekly_update(float current, float incoming) {
  const float next = std::max(0.0f, incoming);
  return next + 1e-3f >= std::max(0.0f, current);
}

float effective_rain_mm_this_week(const OperationalConfig &cfg, uint32_t now_epoch) {
  const uint32_t ttl_s = static_cast<uint32_t>(cfg.global.rain_mm_max_age_hours * 3600.0f);
  if (cfg.rain_mm_last_pushed_epoch == 0 || now_epoch - cfg.rain_mm_last_pushed_epoch > ttl_s)
    return 0.0f;
  return std::max(0.0f, cfg.rain_mm_this_week);
}

float rain_credit_gallons_per_zone(float rain_mm, const GlobalConfig &g) {
  if (g.rain_credit_mm_per_step <= 0.0f)
    return 0.0f;
  return std::max(0.0f, rain_mm * (g.rain_credit_gallons_per_zone_per_step / g.rain_credit_mm_per_step));
}

float effective_weekly_goal(float goal, float rain_mm, const GlobalConfig &g) {
  const float credit = rain_credit_gallons_per_zone(rain_mm, g);
  return std::max(0.0f, goal - credit);
}

bool zone_has_weekly_deficit(const OperationalConfig &cfg, const ZoneRuntime &zone, int zone_id, uint32_t now_epoch,
                             bool ha_feed_valid) {
  const float goal = cfg.zones[zone_id - 1].weekly_goal_gallons;
  if (goal <= 0.0f)
    return false;
  const float rain_mm = effective_rain_mm_this_week(cfg, now_epoch);
  const float target = effective_weekly_goal(goal, rain_mm, cfg.global);
  const float delivered = weekly_delivered_effective(zone, ha_feed_valid);
  return delivered < target;
}

bool zone_cooldown_elapsed(const ZoneRuntime &zone, uint32_t now_epoch, uint32_t cooldown_seconds) {
  if (cooldown_seconds == 0 || zone.last_attempt_epoch == 0)
    return true;
  return now_epoch >= zone.last_attempt_epoch + cooldown_seconds;
}

int pick_next_zone_round_robin(const OperationalConfig &cfg, const ZoneRuntime *zones, uint32_t now_epoch,
                               bool ha_feed_valid, bool respect_cooldown) {
  const uint32_t cooldown_s = static_cast<uint32_t>(cfg.global.attempt_cooldown_minutes * 60.0f);
  const int start = cfg.last_served_zone_id;
  for (int offset = 1; offset <= kNumZones; offset++) {
    const int zid = ((start + offset - 1) % kNumZones) + 1;
    if (((cfg.zones_enabled_bitmask >> (zid - 1)) & 1) == 0)
      continue;
    if (!zone_has_weekly_deficit(cfg, zones[zid - 1], zid, now_epoch, ha_feed_valid))
      continue;
    if (respect_cooldown && !zone_cooldown_elapsed(zones[zid - 1], now_epoch, cooldown_s))
      continue;
    return zid;
  }
  return 0;
}

void update_scheduled_next_epochs(const OperationalConfig &cfg, ZoneRuntime *zones, uint32_t now_epoch,
                                  bool ha_time_valid, bool ha_feed_valid) {
  for (int i = 0; i < kNumZones; i++)
    zones[i].scheduled_next_epoch = 0;

  if (!cfg.fallback_schedule_enabled || now_epoch == 0 || !ha_time_valid)
    return;

  const uint32_t cooldown_s = static_cast<uint32_t>(cfg.global.attempt_cooldown_minutes * 60.0f);
  const float rain_mm = effective_rain_mm_this_week(cfg, now_epoch);

  for (int zid = 1; zid <= kNumZones; zid++) {
    if (((cfg.zones_enabled_bitmask >> (zid - 1)) & 1) == 0)
      continue;
    const auto &zcfg = cfg.zones[zid - 1];
    auto &zs = zones[zid - 1];
    const float goal = zcfg.weekly_goal_gallons;
    const float target = effective_weekly_goal(goal, rain_mm, cfg.global);
    const float delivered = weekly_delivered_effective(zs, ha_feed_valid);
    const bool goal_met = goal > 0.0f && delivered >= target;

    uint32_t next_eligible = 0;
    if (goal_met) {
      next_eligible = next_calendar_week_start_epoch(now_epoch, ha_time_valid);
    } else if (zs.last_attempt_epoch > 0 && cooldown_s > 0) {
      next_eligible = zs.last_attempt_epoch + cooldown_s;
    } else {
      next_eligible = now_epoch;
    }
    zones[zid - 1].scheduled_next_epoch = next_eligible;
  }
}

}  // namespace nedorachio
}  // namespace esphome
