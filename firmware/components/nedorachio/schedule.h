#pragma once

#include "models.h"

namespace esphome {
namespace nedorachio {

bool in_watering_window(int hour, int minute, const GlobalConfig &g);
bool is_blackout_day(int dow_mon0, int blackout_weekday_bitmask);
uint32_t snap_next_start(uint32_t earliest_epoch, const GlobalConfig &g, bool ha_time_valid);
int next_due_zone(const OperationalConfig &cfg, const ZoneRuntime *zones, uint32_t now_epoch, bool ha_time_valid);
void update_scheduled_next_epochs(const OperationalConfig &cfg, ZoneRuntime *zones, uint32_t now_epoch,
                                  bool ha_time_valid);

}  // namespace nedorachio
}  // namespace esphome
