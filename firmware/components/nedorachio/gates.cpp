#include "gates.h"

#include <cstring>

namespace esphome {
namespace nedorachio {

namespace {

bool is_benign(const char *reason) {
  static const char *kBenign[] = {"rain_sensor_wet",
                                  "rain_hold_after_sensor",
                                  "pressure_too_low",
                                  "pressure_too_high",
                                  "alarm_latched",
                                  nullptr};
  for (const char **p = kBenign; *p != nullptr; ++p) {
    if (strcmp(reason, *p) == 0)
      return true;
  }
  return false;
}

}  // namespace

PreflightResult evaluate_preflight(const OperationalConfig &cfg, uint32_t now_epoch, bool rain_sensor_wet,
                                   uint32_t rain_sensor_last_wet_epoch, bool any_alarm_latched,
                                   float static_pressure_psi, bool is_schedule) {
  PreflightResult result;
  const char *reason = "";

  if (!cfg.master_enable)
    reason = "master_enable_off";
  else if (cfg.emergency_stop)
    reason = "emergency_stop_latched";
  else if (rain_sensor_wet)
    reason = "rain_sensor_wet";
  else {
    const uint32_t hold_s = static_cast<uint32_t>(cfg.global.rain_sensor_hold_hours_after_wet * 3600.0f);
    if (rain_sensor_last_wet_epoch > 0 && now_epoch - rain_sensor_last_wet_epoch < hold_s)
      reason = "rain_hold_after_sensor";
  }

  if (reason[0] == '\0' && is_schedule && static_pressure_psi >= kPressureUnavailableBelowPsi) {
    if (static_pressure_psi < cfg.global.pressure_static_min_psi)
      reason = "pressure_too_low";
    else if (static_pressure_psi > cfg.global.pressure_static_max_psi)
      reason = "pressure_too_high";
  }

  if (reason[0] == '\0' && is_schedule && !cfg.fallback_schedule_enabled)
    reason = "schedule_disabled";

  if (reason[0] == '\0' && any_alarm_latched)
    reason = "alarm_latched";

  if (reason[0] != '\0') {
    result.passed = false;
    result.reason = reason;
    result.benign = is_benign(reason);
  }
  return result;
}

}  // namespace nedorachio
}  // namespace esphome
