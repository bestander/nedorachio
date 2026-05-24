#include "schedule.h"

#include <algorithm>
#include <ctime>

namespace esphome {
namespace nedorachio {

namespace {

uint32_t effective_last_finished(uint32_t last_finished, uint32_t now_epoch, uint32_t fallback_start_epoch,
                                 bool ha_time_valid) {
  if (last_finished != 0)
    return last_finished;
  if (ha_time_valid && now_epoch != 0)
    return now_epoch;
  return fallback_start_epoch;
}

int dow_mon0_from_epoch(uint32_t epoch, bool ha_time_valid) {
  time_t tt = static_cast<time_t>(epoch);
  struct tm lt {};
  if (ha_time_valid) {
    localtime_r(&tt, &lt);
  } else {
    time_t est_epoch = tt - 5 * 3600;
    gmtime_r(&est_epoch, &lt);
  }
  return (lt.tm_wday + 6) % 7;
}

void local_hm_from_epoch(uint32_t epoch, bool ha_time_valid, int &hour, int &minute, int &dow_mon0) {
  time_t tt = static_cast<time_t>(epoch);
  struct tm lt {};
  if (ha_time_valid) {
    localtime_r(&tt, &lt);
  } else {
    time_t est_epoch = tt - 5 * 3600;
    gmtime_r(&est_epoch, &lt);
  }
  hour = lt.tm_hour;
  minute = lt.tm_min;
  dow_mon0 = (lt.tm_wday + 6) % 7;
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

uint32_t snap_next_start(uint32_t earliest_epoch, const GlobalConfig &g, bool ha_time_valid) {
  const int start_min_cfg = g.schedule_start_hour * 60 + g.schedule_start_minute;
  const int end_min_cfg = g.schedule_end_hour * 60 + g.schedule_end_minute;
  if (start_min_cfg == end_min_cfg)
    return 0;

  uint32_t t = (earliest_epoch / 60u) * 60u;
  if (t < earliest_epoch)
    t += 60;

  for (int iter = 0; iter < 20160; iter++) {
    int hour = 0;
    int minute = 0;
    int dow_mon0 = 0;
    local_hm_from_epoch(t, ha_time_valid, hour, minute, dow_mon0);
    const int now_min = hour * 60 + minute;
    bool in_window;
    if (start_min_cfg < end_min_cfg) {
      in_window = now_min >= start_min_cfg && now_min < end_min_cfg;
    } else {
      in_window = now_min >= start_min_cfg || now_min < end_min_cfg;
    }
    const bool blackout = is_blackout_day(dow_mon0, g.blackout_weekday_bitmask);
    if (in_window && !blackout)
      return t;
    t += 60;
  }
  return 0;
}

int next_due_zone(const OperationalConfig &cfg, const ZoneRuntime *zones, uint32_t now_epoch, bool ha_time_valid) {
  for (int zid = 1; zid <= kNumZones; zid++) {
    if (((cfg.zones_enabled_bitmask >> (zid - 1)) & 1) == 0)
      continue;
    const auto &zcfg = cfg.zones[zid - 1];
    const uint32_t last =
        effective_last_finished(zones[zid - 1].last_finished_epoch, now_epoch, cfg.fallback_start_epoch, ha_time_valid);
    const uint32_t interval_s = static_cast<uint32_t>(zcfg.min_interval_hours * 3600.0f);
    if (now_epoch >= last + interval_s)
      return zid;
  }
  return 0;
}

void update_scheduled_next_epochs(const OperationalConfig &cfg, ZoneRuntime *zones, uint32_t now_epoch,
                                  bool ha_time_valid) {
  for (int i = 0; i < kNumZones; i++)
    zones[i].scheduled_next_epoch = 0;

  if (!cfg.fallback_schedule_enabled || now_epoch == 0)
    return;

  struct Ideal {
    uint32_t ideal;
    int zid;
  };
  Ideal ideals[kNumZones];
  int n = 0;

  for (int zid = 1; zid <= kNumZones; zid++) {
    if (((cfg.zones_enabled_bitmask >> (zid - 1)) & 1) == 0)
      continue;
    const auto &zcfg = cfg.zones[zid - 1];
    if (zcfg.min_interval_hours <= 0.0f)
      continue;
    uint32_t last =
        effective_last_finished(zones[zid - 1].last_finished_epoch, now_epoch, cfg.fallback_start_epoch, ha_time_valid);
    const uint32_t interval_s = static_cast<uint32_t>(zcfg.min_interval_hours * 3600.0f);
    uint32_t raw_due = last + interval_s;
    const uint32_t ideal = snap_next_start(raw_due, cfg.global, ha_time_valid);
    if (ideal != 0) {
      ideals[n++] = {ideal, zid};
    }
  }

  std::sort(ideals, ideals + n, [](const Ideal &a, const Ideal &b) {
    if (a.ideal != b.ideal)
      return a.ideal < b.ideal;
    return a.zid < b.zid;
  });

  uint32_t cursor = 0;
  const uint32_t dur_s = std::max(60u, static_cast<uint32_t>(cfg.global.maximum_runtime_minutes * 60.0f));
  int assigned_zid[kNumZones + 1] = {0};
  uint32_t assigned_epoch[kNumZones + 1] = {0};

  for (int i = 0; i < n; i++) {
    const uint32_t ideal = ideals[i].ideal;
    const int zid = ideals[i].zid;
    uint32_t merged = ideal;
    if (ideal <= now_epoch)
      merged = (now_epoch / 60u) * 60u;
    if (cursor > merged)
      merged = cursor;
    const uint32_t actual = snap_next_start(merged, cfg.global, ha_time_valid);
    if (actual == 0)
      continue;
    assigned_zid[zid] = zid;
    assigned_epoch[zid] = actual;
    uint32_t next_cursor = actual + dur_s;
    const uint32_t snapped = snap_next_start(next_cursor, cfg.global, ha_time_valid);
    cursor = snapped != 0 ? snapped : next_cursor;
  }

  for (int zid = 1; zid <= kNumZones; zid++) {
    if (assigned_zid[zid] != 0)
      zones[zid - 1].scheduled_next_epoch = assigned_epoch[zid];
  }
}

}  // namespace nedorachio
}  // namespace esphome
