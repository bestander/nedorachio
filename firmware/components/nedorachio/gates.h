#pragma once

#include "models.h"

namespace esphome {
namespace nedorachio {

struct PreflightResult {
  bool passed{true};
  const char *reason{""};
  bool benign{false};
};

PreflightResult evaluate_preflight(const OperationalConfig &cfg, uint32_t now_epoch, bool rain_sensor_wet,
                                   uint32_t rain_sensor_last_wet_epoch, uint32_t rain_forecast_last_high_epoch,
                                   bool any_alarm_latched, float static_pressure_psi, bool is_schedule);

}  // namespace nedorachio
}  // namespace esphome
