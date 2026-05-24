#pragma once

#include <cstdint>
#include <string>

namespace esphome {
namespace nedorachio {

static constexpr int kNumZones = 8;
static constexpr uint32_t kFallbackStartEpoch = 1780329600u;
// Below this reading the line is treated as depressurized (pump off); skip static PSI gates.
static constexpr float kPressureUnavailableBelowPsi = 5.0f;

struct ZoneConfig {
  bool enabled{false};
  int schedule_mode{1};  // 0=minutes, 1=gallons
  float goal_gallons{0.0f};
  float cycle_gallons{0.0f};
  float soak_minutes{0.0f};
  float min_interval_hours{72.0f};
  float start_minimum_psi{35.0f};
  float start_maximum_psi{85.0f};
  float minimum_running_psi{20.0f};
  float minimum_running_psi_grace_seconds{60.0f};
  float min_flow_gpm{0.2f};
  float max_flow_gpm{12.0f};
  float total_minutes{20.0f};
  float cycle_minutes{10.0f};
};

struct GlobalConfig {
  int schedule_start_hour{23};
  int schedule_start_minute{0};
  int schedule_end_hour{9};
  int schedule_end_minute{0};
  int blackout_weekday_bitmask{0};
  float attempt_cooldown_minutes{20.0f};
  float maximum_runtime_minutes{60.0f};
  float no_flow_grace_s{60.0f};
  float no_flow_sustain_s{30.0f};
  float rain_mm_threshold_48h{5.0f};
  float rain_hold_hours_after_forecast{24.0f};
  float rain_hold_hours_after_sensor{24.0f};
  float rain_mm_max_age_hours{12.0f};
  float pulses_per_gallon{344.4f};
  float phantom_flow_gpm{0.5f};
  float pressure_static_min_psi{30.0f};
  float pressure_static_max_psi{80.0f};
  float pressure_high_psi{90.0f};
};

struct OperationalConfig {
  GlobalConfig global{};
  ZoneConfig zones[kNumZones]{};
  int zones_enabled_bitmask{0};
  bool fallback_schedule_enabled{true};
  bool master_enable{true};
  bool emergency_stop{false};
  float rain_mm_last_48h{0.0f};
  uint32_t rain_mm_last_pushed_epoch{0};
  uint32_t fallback_start_epoch{kFallbackStartEpoch};
};

struct ZoneRuntime {
  uint32_t last_finished_epoch{0};
  uint32_t scheduled_next_epoch{0};
  float cycle_delivered_gallons{0.0f};
  bool actual_state{false};
};

struct RuntimeState {
  int version{1};
  uint32_t updated_epoch{0};
  ZoneRuntime zones[kNumZones]{};
  uint32_t rain_sensor_last_wet_epoch{0};
  uint32_t rain_forecast_last_high_epoch{0};
  uint32_t last_non_completed_attempt_epoch{0};
};

}  // namespace nedorachio
}  // namespace esphome
