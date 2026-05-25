#pragma once

#include "models.h"

#include "esphome/components/output/binary_output.h"
#include "esphome/components/sensor/sensor.h"
#include "esphome/components/binary_sensor/binary_sensor.h"

namespace esphome {
namespace nedorachio {

enum class EnginePhase {
  IDLE,
  PREFLIGHT_WAIT,
  RUNNING,
};

class IrrigationEngine {
 public:
  void set_zone_output(int index, esphome::output::BinaryOutput *output) {
    if (index >= 0 && index < kNumZones)
      this->outputs_[index] = output;
  }
  void set_sensors(esphome::sensor::Sensor *pressure, esphome::sensor::Sensor *flow_gpm,
                   esphome::sensor::Sensor *flow_total, esphome::binary_sensor::BinarySensor *rain) {
    this->pressure_ = pressure;
    this->flow_gpm_ = flow_gpm;
    this->flow_total_ = flow_total;
    this->rain_ = rain;
  }

  void apply_config(const OperationalConfig &cfg) { this->config_ = cfg; }
  void refresh_schedule_plan(uint32_t now_epoch, bool ha_time_valid) { this->update_plan(now_epoch, ha_time_valid); }
  void set_fallback_schedule_enabled(bool enabled) { this->config_.fallback_schedule_enabled = enabled; }
  bool fallback_schedule_enabled() const { return this->config_.fallback_schedule_enabled; }
  void set_rain_mm_this_week(float mm, uint32_t pushed_epoch) {
    this->config_.rain_mm_this_week = mm;
    this->config_.rain_mm_last_pushed_epoch = pushed_epoch;
  }
  void apply_runtime(const RuntimeState &state);
  RuntimeState runtime_state(uint32_t now_epoch) const;

  void on_zone_weekly_delivered(int zone_id, float gallons, uint32_t now_epoch);

  uint32_t zone_last_finished_epoch(int zone_id) const;
  void set_zone_last_finished(int zone_id, uint32_t epoch, uint32_t now_epoch, bool ha_time_valid);

  void tick(uint32_t now_epoch, uint32_t now_ms, bool ha_time_valid);

  bool request_zone_on(int zone_id, uint32_t now_epoch);
  bool request_zone_off(int zone_id, uint32_t now_epoch);
  bool zone_actual_state(int zone_id) const;

  int currently_on_zone() const { return this->currently_on_zone_; }
  uint32_t zone_scheduled_next(int zone_id) const {
    if (zone_id < 1 || zone_id > kNumZones)
      return 0;
    return this->zones_[zone_id - 1].scheduled_next_epoch;
  }
  const char *current_phase() const;
  const char *last_run_outcome() const { return this->last_run_outcome_; }
  const char *tracking_source() const { return this->tracking_source_; }
  int last_completed_zone() const { return this->last_completed_zone_; }
  float last_run_gallons() const { return this->last_run_gallons_; }
  uint32_t gallons_completion_sequence() const { return this->gallons_completion_sequence_; }
  float zone_gallons_total(int zone_id) const;
  void set_zone_gallons_total(int zone_id, float gallons);
  float zone_weekly_goal_gallons(int zone_id) const;
  float rain_credit_mm_per_step() const;
  float rain_credit_gallons_per_zone_per_step() const;
 private:
  void drive_zone(int zone_id, bool on, bool stamp_finished);
  void weekly_budget_evaluator(uint32_t now_epoch, bool ha_time_valid);
  void update_plan(uint32_t now_epoch, bool ha_time_valid);
  void apply_week_reset_if_needed_(uint32_t now_epoch, bool ha_time_valid);
  bool ha_weekly_feed_valid_(uint32_t now_epoch) const;
  void run_safety(uint32_t now_epoch, uint32_t now_ms);
  void start_weekly_run(int zone_id, uint32_t now_epoch);
  void step_run(uint32_t now_epoch, uint32_t now_ms);
  void finish_attempt_(int zone_id, uint32_t now_epoch, bool completed);
  void sync_weekly_delivered_(int zone_id, float gallons_done, uint32_t now_epoch);
  bool preflight(uint32_t now_epoch, bool is_schedule);
  void set_phase_(EnginePhase next, const char *reason);
  void record_gallons_delivery_(int zone_id, float gallons);
  float read_pressure(bool zone_on) const;
  float read_flow_gpm() const;
  float read_flow_total() const;
  float integrate_run_gallons_() const;

  esphome::output::BinaryOutput *outputs_[kNumZones]{};
  esphome::sensor::Sensor *pressure_{nullptr};
  esphome::sensor::Sensor *flow_gpm_{nullptr};
  esphome::sensor::Sensor *flow_total_{nullptr};
  esphome::binary_sensor::BinarySensor *rain_{nullptr};

  OperationalConfig config_{};
  ZoneRuntime zones_[kNumZones]{};
  uint32_t rain_sensor_last_wet_epoch_{0};
  uint32_t rain_forecast_last_high_epoch_{0};
  int week_id_shadow_{0};
  char tracking_source_[8]{"local"};
  uint32_t ha_weekly_last_update_epoch_{0};

  int currently_on_zone_{0};
  uint32_t zone_started_at_ms_{0};
  bool stamp_finished_on_zone_off_{true};
  bool any_alarm_latched_{false};
  bool skip_next_run_pending_{false};
  bool is_manual_run_{false};

  EnginePhase phase_{EnginePhase::IDLE};
  int run_zone_id_{0};
  float run_goal_gallons_{0};
  float run_gallons_done_{0};
  float run_start_delivered_{0};
  float run_started_total_{0};
  int preflight_wait_left_{0};
  bool run_cancel_requested_{false};
  char run_cancel_cause_[32]{};
  char last_run_outcome_[48]{""};
  int last_completed_zone_{0};
  float last_run_gallons_{0.0f};
  uint32_t gallons_completion_sequence_{0};

  uint32_t tick_count_{0};
  bool last_ha_time_valid_{false};

  uint32_t no_flow_first_ms_{0};
  uint32_t phantom_first_ms_{0};
};

}  // namespace nedorachio
}  // namespace esphome
