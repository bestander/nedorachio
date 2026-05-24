#pragma once

#include "engine.h"
#include "json_io.h"

#include "esphome/components/time/real_time_clock.h"
#include "esphome/core/component.h"

namespace esphome {
namespace nedorachio {

struct HaPublishRequest {
  int zone{0};
  uint32_t epoch{0};
};

class NedorachioComponent : public esphome::Component {
 public:
  void set_time(esphome::time::RealTimeClock *time) { this->time_ = time; }
  void set_engine(IrrigationEngine *engine) { this->engine_ = engine; }
  void set_config_profile(const std::string &profile) { this->config_profile_ = profile; }

  void setup() override;
  void loop() override;

  void on_zone_last_watering(int zone_id, uint32_t epoch);

  HaPublishRequest consume_ha_publish_request();

  bool request_zone_on(int zone_id);
  bool request_zone_off(int zone_id);
  bool get_zone_actual_state(int zone_id) const;

  float get_zone_scheduled_next(int zone_id) const;
  int get_currently_running_zone() const;
  const char *get_current_phase() const;
  const char *get_last_run_outcome() const;
  uint32_t now_epoch_for_rain() const { return this->now_epoch_(); }

  void set_fallback_schedule_enabled(bool enabled) {
    if (this->engine_ != nullptr)
      this->engine_->set_fallback_schedule_enabled(enabled);
  }
  bool fallback_schedule_enabled() const {
    if (this->engine_ == nullptr)
      return true;
    return this->engine_->fallback_schedule_enabled();
  }
  void set_rain_mm_last_48h(float mm, uint32_t epoch) {
    if (this->engine_ != nullptr)
      this->engine_->set_rain_mm_last_48h(mm, epoch);
  }

 protected:
  uint32_t now_epoch_() const;
  bool ha_time_valid_() const;
  void apply_config_profile_();
  void sync_ha_publish_from_engine_();

  esphome::time::RealTimeClock *time_{nullptr};
  IrrigationEngine *engine_{nullptr};
  std::string config_profile_;
  uint32_t last_tick_ms_{0};
  uint32_t published_last_finished_[kNumZones]{};
  HaPublishRequest pending_ha_publish_{};
};

}  // namespace nedorachio
}  // namespace esphome
