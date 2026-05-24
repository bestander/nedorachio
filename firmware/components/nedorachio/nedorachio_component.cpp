#include "nedorachio_component.h"

#include "esphome/core/log.h"

#include <Arduino.h>

namespace esphome {
namespace nedorachio {

static const char *const TAG = "nedorachio";

void NedorachioComponent::setup() {
  this->last_tick_ms_ = millis();
  this->apply_config_profile_();
  ESP_LOGI(TAG, "Nedorachio component ready");
}

uint32_t NedorachioComponent::now_epoch_() const {
  if (this->time_ == nullptr)
    return 0;
  auto now = this->time_->now();
  if (!now.is_valid())
    return 0;
  return static_cast<uint32_t>(now.timestamp);
}

bool NedorachioComponent::ha_time_valid_() const {
  if (this->time_ == nullptr)
    return false;
  return this->time_->now().is_valid();
}

void NedorachioComponent::apply_config_profile_() {
  if (this->engine_ == nullptr)
    return;
  if (this->config_profile_.empty()) {
    ESP_LOGW(TAG, "config_profile empty — engine not configured");
    return;
  }
  OperationalConfig cfg;
  if (parse_config_json(this->config_profile_, cfg)) {
    this->engine_->apply_config(cfg);
    this->engine_->refresh_schedule_plan(this->now_epoch_(), this->ha_time_valid_());
    ESP_LOGI(TAG,
             "Config profile loaded (%u bytes) zones=0x%02x z1_interval=%.0fh z1_next=%u window=%02d:%02d-%02d:%02d",
             (unsigned) this->config_profile_.size(), cfg.zones_enabled_bitmask, cfg.zones[0].min_interval_hours,
             this->engine_->zone_scheduled_next(1), cfg.global.schedule_start_hour, cfg.global.schedule_start_minute,
             cfg.global.schedule_end_hour, cfg.global.schedule_end_minute);
  } else {
    ESP_LOGW(TAG, "Failed to parse config_profile JSON (%u bytes)", (unsigned) this->config_profile_.size());
  }
}

void NedorachioComponent::on_zone_last_watering(int zone_id, uint32_t epoch) {
  if (this->engine_ == nullptr || zone_id < 1 || zone_id > kNumZones)
    return;
  const uint32_t now = this->now_epoch_();
  const bool ha_ok = this->ha_time_valid_();
  this->engine_->set_zone_last_finished(zone_id, epoch, now, ha_ok);
  this->published_last_finished_[zone_id - 1] = epoch;
  ESP_LOGI(TAG, "HA zone %d last_watering=%u ha_time=%d epoch_now=%u scheduled_next=%u", zone_id, epoch, ha_ok, now,
           this->engine_->zone_scheduled_next(zone_id));
}

HaPublishRequest NedorachioComponent::consume_ha_publish_request() {
  HaPublishRequest req = this->pending_ha_publish_;
  this->pending_ha_publish_ = {};
  return req;
}

void NedorachioComponent::sync_ha_publish_from_engine_() {
  if (this->engine_ == nullptr)
    return;
  for (int zid = 1; zid <= kNumZones; zid++) {
    const uint32_t lf = this->engine_->zone_last_finished_epoch(zid);
    if (lf == this->published_last_finished_[zid - 1])
      continue;
    this->published_last_finished_[zid - 1] = lf;
    if (this->pending_ha_publish_.zone == 0) {
      this->pending_ha_publish_.zone = zid;
      this->pending_ha_publish_.epoch = lf;
      ESP_LOGI(TAG, "queue HA publish zone %d last_watering=%u", zid, lf);
    }
  }
}

void NedorachioComponent::loop() {
  const uint32_t now_ms = millis();
  if (now_ms - this->last_tick_ms_ < 1000)
    return;
  this->last_tick_ms_ = now_ms;

  if (this->engine_ != nullptr) {
    this->engine_->tick(this->now_epoch_(), now_ms, this->ha_time_valid_());
    this->sync_ha_publish_from_engine_();
  }
}

float NedorachioComponent::get_zone_scheduled_next(int zone_id) const {
  if (this->engine_ == nullptr || zone_id < 1 || zone_id > kNumZones)
    return 0;
  return static_cast<float>(this->engine_->zone_scheduled_next(zone_id));
}

int NedorachioComponent::get_currently_running_zone() const {
  if (this->engine_ == nullptr)
    return 0;
  return this->engine_->currently_on_zone();
}

const char *NedorachioComponent::get_current_phase() const {
  if (this->engine_ == nullptr)
    return "idle";
  return this->engine_->current_phase();
}

const char *NedorachioComponent::get_last_run_outcome() const {
  if (this->engine_ == nullptr)
    return "";
  return this->engine_->last_run_outcome();
}

bool NedorachioComponent::request_zone_on(int zone_id) {
  if (this->engine_ == nullptr) {
    ESP_LOGW(TAG, "zone_on(%d): engine not ready", zone_id);
    return false;
  }
  const uint32_t epoch = this->now_epoch_();
  const bool ok = this->engine_->request_zone_on(zone_id, epoch);
  ESP_LOGI(TAG, "HA zone_on(%d) epoch=%u ha_time=%d -> %s phase=%s actual=%d", zone_id, epoch,
           this->ha_time_valid_(), ok ? "accepted" : "rejected", this->engine_->current_phase(),
           this->engine_->zone_actual_state(zone_id));
  return ok;
}

bool NedorachioComponent::request_zone_off(int zone_id) {
  if (this->engine_ == nullptr) {
    ESP_LOGW(TAG, "zone_off(%d): engine not ready", zone_id);
    return false;
  }
  const uint32_t epoch = this->now_epoch_();
  const bool ok = this->engine_->request_zone_off(zone_id, epoch);
  ESP_LOGI(TAG, "HA zone_off(%d) epoch=%u -> %s phase=%s actual=%d on_zone=%d", zone_id, epoch,
           ok ? "accepted" : "rejected", this->engine_->current_phase(), this->engine_->zone_actual_state(zone_id),
           this->engine_->currently_on_zone());
  return ok;
}

bool NedorachioComponent::get_zone_actual_state(int zone_id) const {
  if (this->engine_ == nullptr)
    return false;
  return this->engine_->zone_actual_state(zone_id);
}

}  // namespace nedorachio
}  // namespace esphome
