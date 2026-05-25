#pragma once

#include "models.h"

namespace esphome {
namespace nedorachio {

bool in_watering_window(int hour, int minute, const GlobalConfig &g);
bool is_blackout_day(int dow_mon0, int blackout_weekday_bitmask);
int calendar_week_id(uint32_t epoch, bool ha_time_valid);
int maybe_apply_week_reset(ZoneRuntime *zones, int week_id_shadow, int current_week_id);
float weekly_delivered_effective(const ZoneRuntime &zone, bool ha_feed_valid);
bool accept_ha_weekly_update(float current, float incoming);
float effective_rain_mm_this_week(const OperationalConfig &cfg, uint32_t now_epoch);
float rain_credit_gallons_per_zone(float rain_mm, const GlobalConfig &g);
float effective_weekly_goal(float goal, float rain_mm, const GlobalConfig &g);
bool zone_has_weekly_deficit(const OperationalConfig &cfg, const ZoneRuntime &zone, int zone_id, uint32_t now_epoch,
                             bool ha_feed_valid);
bool zone_cooldown_elapsed(const ZoneRuntime &zone, uint32_t now_epoch, uint32_t cooldown_seconds);
int pick_next_zone_round_robin(const OperationalConfig &cfg, const ZoneRuntime *zones, uint32_t now_epoch,
                               bool ha_feed_valid, bool respect_cooldown);
void update_scheduled_next_epochs(const OperationalConfig &cfg, ZoneRuntime *zones, uint32_t now_epoch,
                                  bool ha_time_valid, bool ha_feed_valid);

}  // namespace nedorachio
}  // namespace esphome
