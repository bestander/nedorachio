#include "engine.h"

#include "gates.h"
#include "schedule.h"

#include "esphome/core/helpers.h"
#include "esphome/core/log.h"

#include <Arduino.h>

#include <algorithm>
#include <cmath>
#include <cstring>

namespace esphome {
namespace nedorachio {

static const char *const TAG = "nedorachio.engine";

namespace {

const char *phase_name(EnginePhase phase) {
  switch (phase) {
    case EnginePhase::IDLE:
      return "idle";
    case EnginePhase::PREFLIGHT_WAIT:
      return "pre_flight";
    case EnginePhase::RUNNING:
      return "running";
  }
  return "unknown";
}

}  // namespace

void IrrigationEngine::set_phase_(EnginePhase next, const char *reason) {
  if (this->phase_ == next)
    return;
  ESP_LOGI(TAG, "phase %s -> %s (%s) run_zone=%d manual=%d on_zone=%d",
           phase_name(this->phase_), phase_name(next), reason, this->run_zone_id_, this->is_manual_run_,
           this->currently_on_zone_);
  this->phase_ = next;
}

void IrrigationEngine::record_gallons_delivery_(int zone_id, float gallons) {
  if (zone_id < 1 || zone_id > kNumZones || gallons <= 0.0f)
    return;
  this->zones_[zone_id - 1].gallons_total += gallons;
  this->last_completed_zone_ = zone_id;
  this->last_run_gallons_ = gallons;
  this->gallons_completion_sequence_++;
  ESP_LOGI(TAG, "gallons event z=%d delivered=%.2f seq=%u", zone_id, gallons, this->gallons_completion_sequence_);
}

void IrrigationEngine::apply_runtime(const RuntimeState &state) {
  ESP_LOGI(TAG, "apply_runtime updated_epoch=%u rain_wet=%u rain_forecast=%u week_id=%d last_served=%d",
           state.updated_epoch, state.rain_sensor_last_wet_epoch, state.rain_forecast_last_high_epoch,
           state.week_id_shadow, state.last_served_zone_id);
  for (int i = 0; i < kNumZones; i++) {
    this->zones_[i].last_finished_epoch = state.zones[i].last_finished_epoch;
    this->zones_[i].weekly_delivered_shadow = state.zones[i].weekly_delivered_shadow;
    this->zones_[i].last_attempt_epoch = state.zones[i].last_attempt_epoch;
    ESP_LOGD(TAG, "  zone %d last_finished=%u weekly_shadow=%.2f last_attempt=%u", i + 1,
             state.zones[i].last_finished_epoch, state.zones[i].weekly_delivered_shadow,
             state.zones[i].last_attempt_epoch);
  }
  this->rain_sensor_last_wet_epoch_ = state.rain_sensor_last_wet_epoch;
  this->rain_forecast_last_high_epoch_ = state.rain_forecast_last_high_epoch;
  this->week_id_shadow_ = state.week_id_shadow;
  this->config_.last_served_zone_id = state.last_served_zone_id;
}

RuntimeState IrrigationEngine::runtime_state(uint32_t now_epoch) const {
  RuntimeState state;
  state.version = 2;
  state.updated_epoch = now_epoch;
  for (int i = 0; i < kNumZones; i++)
    state.zones[i] = this->zones_[i];
  state.rain_sensor_last_wet_epoch = this->rain_sensor_last_wet_epoch_;
  state.rain_forecast_last_high_epoch = this->rain_forecast_last_high_epoch_;
  state.week_id_shadow = this->week_id_shadow_;
  state.last_served_zone_id = this->config_.last_served_zone_id;
  return state;
}

void IrrigationEngine::on_zone_weekly_delivered(int zone_id, float gallons, uint32_t now_epoch) {
  if (zone_id < 1 || zone_id > kNumZones)
    return;
  auto &zs = this->zones_[zone_id - 1];
  const float ha_val = std::max(0.0f, gallons);
  zs.ha_weekly_delivered = ha_val;
  zs.weekly_delivered_shadow = std::max(zs.weekly_delivered_shadow, ha_val);
  this->ha_weekly_last_update_epoch_ = now_epoch;
  strncpy(this->tracking_source_, "ha", sizeof(this->tracking_source_));
  ESP_LOGI(TAG, "HA weekly delivered z=%d gallons=%.2f epoch=%u", zone_id, ha_val, now_epoch);
}

bool IrrigationEngine::ha_weekly_feed_valid_(uint32_t now_epoch) const {
  if (!this->last_ha_time_valid_ || this->ha_weekly_last_update_epoch_ == 0)
    return false;
  if (now_epoch <= this->ha_weekly_last_update_epoch_)
    return true;
  return (now_epoch - this->ha_weekly_last_update_epoch_) <= kHaWeeklyStalenessSeconds;
}

void IrrigationEngine::apply_week_reset_if_needed_(uint32_t now_epoch, bool ha_time_valid) {
  if (now_epoch == 0)
    return;
  const int current = calendar_week_id(now_epoch, ha_time_valid);
  const int new_id = maybe_apply_week_reset(this->zones_, this->week_id_shadow_, current);
  if (new_id != this->week_id_shadow_) {
    this->week_id_shadow_ = new_id;
    this->config_.last_served_zone_id = 0;
    ESP_LOGI(TAG, "week reset week_id=%d", new_id);
  }
}

uint32_t IrrigationEngine::zone_last_finished_epoch(int zone_id) const {
  if (zone_id < 1 || zone_id > kNumZones)
    return 0;
  return this->zones_[zone_id - 1].last_finished_epoch;
}

float IrrigationEngine::zone_gallons_total(int zone_id) const {
  if (zone_id < 1 || zone_id > kNumZones)
    return 0.0f;
  return this->zones_[zone_id - 1].gallons_total;
}

void IrrigationEngine::set_zone_gallons_total(int zone_id, float gallons) {
  if (zone_id < 1 || zone_id > kNumZones)
    return;
  this->zones_[zone_id - 1].gallons_total = std::max(0.0f, gallons);
}

void IrrigationEngine::set_zone_last_finished(int zone_id, uint32_t epoch, uint32_t now_epoch, bool ha_time_valid) {
  if (zone_id < 1 || zone_id > kNumZones)
    return;
  this->zones_[zone_id - 1].last_finished_epoch = epoch;
  this->update_plan(now_epoch, ha_time_valid);
}

const char *IrrigationEngine::current_phase() const {
  return phase_name(this->phase_);
}

float IrrigationEngine::read_pressure(bool zone_on) const {
  if (this->pressure_ == nullptr || std::isnan(this->pressure_->get_state()))
    return zone_on ? 45.0f : 50.0f;
  return this->pressure_->get_state();
}

float IrrigationEngine::read_flow_gpm() const {
  if (this->flow_gpm_ == nullptr || std::isnan(this->flow_gpm_->get_state()))
    return 0.0f;
  return this->flow_gpm_->get_state();
}

float IrrigationEngine::read_flow_total() const {
  if (this->flow_total_ == nullptr || std::isnan(this->flow_total_->get_state()))
    return 0.0f;
  return this->flow_total_->get_state();
}

float IrrigationEngine::integrate_run_gallons_() const {
  const float ppg = this->config_.global.pulses_per_gallon;
  const float total = this->read_flow_total();
  const float run_pulses = std::max(0.0f, total - this->run_started_total_);
  return (ppg > 0.0f) ? run_pulses / ppg : 0.0f;
}

void IrrigationEngine::sync_weekly_delivered_(int zone_id, float gallons_done) {
  if (zone_id < 1 || zone_id > kNumZones)
    return;
  this->zones_[zone_id - 1].weekly_delivered_shadow = std::max(this->zones_[zone_id - 1].weekly_delivered_shadow, gallons_done);
}

void IrrigationEngine::finish_attempt_(int zone_id, uint32_t now_epoch, bool completed) {
  if (zone_id < 1 || zone_id > kNumZones)
    return;
  auto &zs = this->zones_[zone_id - 1];
  if (now_epoch > 0)
    zs.last_attempt_epoch = now_epoch;
  if (completed && now_epoch > 0)
    zs.last_finished_epoch = now_epoch;
}

void IrrigationEngine::drive_zone(int zone_id, bool on, bool stamp_finished) {
  if (zone_id < 1 || zone_id > kNumZones) {
    ESP_LOGW(TAG, "drive_zone ignored: invalid zone_id=%d", zone_id);
    return;
  }
  if (this->outputs_[zone_id - 1] == nullptr) {
    ESP_LOGW(TAG, "drive_zone ignored: zone %d output not wired", zone_id);
    return;
  }
  const int prev_on = this->currently_on_zone_;
  const bool prev_actual = this->zones_[zone_id - 1].actual_state;
  if (stamp_finished)
    this->stamp_finished_on_zone_off_ = stamp_finished;
  if (on) {
    if (this->currently_on_zone_ != 0 && this->currently_on_zone_ != zone_id) {
      ESP_LOGI(TAG, "drive_zone z=%d ON: turning off previous zone %d", zone_id, this->currently_on_zone_);
      this->outputs_[this->currently_on_zone_ - 1]->turn_off();
      this->zones_[this->currently_on_zone_ - 1].actual_state = false;
    }
    this->outputs_[zone_id - 1]->turn_on();
    this->zones_[zone_id - 1].actual_state = true;
    this->currently_on_zone_ = zone_id;
    this->zone_started_at_ms_ = millis();
    this->no_flow_first_ms_ = 0;
  } else if (this->currently_on_zone_ == zone_id) {
    this->outputs_[zone_id - 1]->turn_off();
    this->zones_[zone_id - 1].actual_state = false;
    this->currently_on_zone_ = 0;
    this->zone_started_at_ms_ = 0;
    this->stamp_finished_on_zone_off_ = true;
  } else {
    ESP_LOGD(TAG, "drive_zone z=%d OFF ignored (currently_on=%d)", zone_id, this->currently_on_zone_);
    return;
  }
  ESP_LOGI(TAG, "drive_zone z=%d %s stamp=%d on_zone %d->%d actual_z%d %d->%d phase=%s",
           zone_id, on ? "ON" : "OFF", stamp_finished, prev_on, this->currently_on_zone_, zone_id, prev_actual,
           this->zones_[zone_id - 1].actual_state, phase_name(this->phase_));
}

bool IrrigationEngine::preflight(uint32_t now_epoch, bool is_schedule) {
  const bool rain_wet = this->rain_ != nullptr && this->rain_->state;
  const float static_pressure = this->read_pressure(false);
  auto result = evaluate_preflight(this->config_, now_epoch, rain_wet, this->rain_sensor_last_wet_epoch_,
                                   this->any_alarm_latched_, static_pressure, is_schedule);
  if (!result.passed) {
    ESP_LOGW(TAG,
             "preflight FAIL reason=%s benign=%d manual=%d epoch=%u pressure=%.1f rain_wet=%d rain_mm_week=%.1f "
             "alarm=%d master=%d e_stop=%d schedule_en=%d",
             result.reason, result.benign, !is_schedule, now_epoch, static_pressure, rain_wet,
             this->config_.rain_mm_this_week, this->any_alarm_latched_, this->config_.master_enable,
             this->config_.emergency_stop, this->config_.fallback_schedule_enabled);
  } else {
    ESP_LOGI(TAG, "preflight PASS manual=%d epoch=%u pressure=%.1f rain_wet=%d", !is_schedule, now_epoch,
             static_pressure, rain_wet);
  }
  return result.passed;
}

void IrrigationEngine::run_safety(uint32_t now_epoch, uint32_t now_ms) {
  const float gpm = this->read_flow_gpm();
  if (this->currently_on_zone_ == 0) {
    if (gpm > this->config_.global.phantom_flow_gpm) {
      if (this->phantom_first_ms_ == 0)
        this->phantom_first_ms_ = now_ms;
      if (now_ms - this->phantom_first_ms_ > 5 * 60 * 1000) {
        ESP_LOGW(TAG, "safety: phantom flow latched (gpm=%.2f > %.2f)", gpm, this->config_.global.phantom_flow_gpm);
        this->any_alarm_latched_ = true;
      }
    } else {
      this->phantom_first_ms_ = 0;
    }
    return;
  }

  const uint32_t since_start = now_ms - this->zone_started_at_ms_;
  const int zid = this->currently_on_zone_;
  const auto &zcfg = this->config_.zones[zid - 1];
  const uint32_t startup_grace_ms = static_cast<uint32_t>(this->config_.global.no_flow_grace_s * 1000.0f);

  if (since_start >= startup_grace_ms && gpm < zcfg.min_flow_gpm) {
    if (this->no_flow_first_ms_ == 0)
      this->no_flow_first_ms_ = now_ms;
    if (now_ms - this->no_flow_first_ms_ >= static_cast<uint32_t>(this->config_.global.no_flow_sustain_s * 1000.0f)) {
      ESP_LOGW(TAG, "safety: no_flow z=%d gpm=%.2f min=%.2f since_start=%ums", zid, gpm, zcfg.min_flow_gpm,
               since_start);
      this->run_cancel_requested_ = true;
      strncpy(this->run_cancel_cause_, "no_flow", sizeof(this->run_cancel_cause_));
      this->no_flow_first_ms_ = 0;
    }
  } else {
    this->no_flow_first_ms_ = 0;
  }

  if (this->rain_ != nullptr && this->rain_->state) {
    ESP_LOGW(TAG, "safety: rain sensor wet z=%d", zid);
    this->rain_sensor_last_wet_epoch_ = now_epoch;
    this->run_cancel_requested_ = true;
    strncpy(this->run_cancel_cause_, "rain", sizeof(this->run_cancel_cause_));
  }

  const uint32_t cap_ms = static_cast<uint32_t>(this->config_.global.max_attempt_minutes * 60.0f * 1000.0f);
  if (since_start >= cap_ms) {
    ESP_LOGW(TAG, "safety: attempt cap z=%d since_start=%ums cap=%ums", zid, since_start, cap_ms);
    this->run_cancel_requested_ = true;
    strncpy(this->run_cancel_cause_, "attempt_cap", sizeof(this->run_cancel_cause_));
  }
}

void IrrigationEngine::update_plan(uint32_t now_epoch, bool ha_time_valid) {
  if (this->currently_on_zone_ != 0)
    return;
  this->apply_week_reset_if_needed_(now_epoch, ha_time_valid);
  const bool feed_valid = this->ha_weekly_feed_valid_(now_epoch);
  if (!feed_valid)
    strncpy(this->tracking_source_, "local", sizeof(this->tracking_source_));
  update_scheduled_next_epochs(this->config_, this->zones_, now_epoch, ha_time_valid, feed_valid);
}

void IrrigationEngine::start_weekly_run(int zone_id, uint32_t now_epoch) {
  const auto &zcfg = this->config_.zones[zone_id - 1];
  const bool feed_valid = this->ha_weekly_feed_valid_(now_epoch);
  const float run_start = weekly_delivered_effective(this->zones_[zone_id - 1], feed_valid);
  const float rain_mm = effective_rain_mm_this_week(this->config_, now_epoch);
  float run_goal = zcfg.weekly_goal_gallons;
  if (!this->is_manual_run_)
    run_goal = effective_weekly_goal(run_goal, rain_mm, this->config_.global);

  this->run_zone_id_ = zone_id;
  this->run_goal_gallons_ = run_goal;
  this->run_start_delivered_ = run_start;
  this->run_gallons_done_ = run_start;
  this->run_started_total_ = this->read_flow_total();
  this->run_cancel_requested_ = false;
  this->run_cancel_cause_[0] = '\0';
  this->stamp_finished_on_zone_off_ = true;
  strncpy(this->last_run_outcome_, "running", sizeof(this->last_run_outcome_));

  const float p = this->read_pressure(false);
  ESP_LOGI(TAG, "start_run z=%d manual=%d goal=%.1fg delivered=%.1fg pressure=%.1f total_pulses=%.0f",
           zone_id, this->is_manual_run_, this->run_goal_gallons_, run_start, p, this->run_started_total_);
  const bool pressure_live = p >= kPressureUnavailableBelowPsi;
  if (!this->is_manual_run_ && pressure_live) {
    if (p < zcfg.start_minimum_psi || p > zcfg.start_maximum_psi) {
      ESP_LOGW(TAG, "start_run z=%d pressure %.1f outside [%.1f, %.1f]", zone_id, p, zcfg.start_minimum_psi,
               zcfg.start_maximum_psi);
      this->run_cancel_requested_ = true;
      strncpy(this->run_cancel_cause_, "start_pressure_out_of_bounds", sizeof(this->run_cancel_cause_));
    }
  } else if (!pressure_live) {
    ESP_LOGI(TAG, "start_run z=%d skipping start pressure check (%.1f PSI depressurized)", zone_id, p);
  }

  this->set_phase_(EnginePhase::RUNNING, "start_weekly_run");
  if (this->run_cancel_requested_) {
    this->finish_attempt_(zone_id, now_epoch, false);
    snprintf(this->last_run_outcome_, sizeof(this->last_run_outcome_), "cancelled_%s", this->run_cancel_cause_);
    this->set_phase_(EnginePhase::IDLE, "start_cancelled");
    this->is_manual_run_ = false;
    return;
  }
  this->drive_zone(zone_id, true, true);
}

void IrrigationEngine::step_run(uint32_t now_epoch, uint32_t now_ms) {
  if (this->phase_ == EnginePhase::PREFLIGHT_WAIT) {
    if (--this->preflight_wait_left_ <= 0) {
      const bool is_schedule = !this->is_manual_run_;
      if (!this->preflight(now_epoch, is_schedule)) {
        this->set_phase_(EnginePhase::IDLE, "pre_flight_failed");
        this->is_manual_run_ = false;
        strncpy(this->last_run_outcome_, "pre_flight_failed", sizeof(this->last_run_outcome_));
        ESP_LOGW(TAG, "run aborted at preflight z=%d outcome=pre_flight_failed", this->run_zone_id_);
        return;
      }
      this->start_weekly_run(this->run_zone_id_, now_epoch);
    }
    return;
  }

  if (this->phase_ != EnginePhase::RUNNING)
    return;

  const int zid = this->run_zone_id_;
  const float session_gal = this->integrate_run_gallons_();
  this->run_gallons_done_ = this->run_start_delivered_ + session_gal;
  this->sync_weekly_delivered_(zid, this->run_gallons_done_);

  if (this->run_cancel_requested_) {
    ESP_LOGW(TAG, "run cancelled z=%d cause=%s manual=%d delivered=%.2f", zid, this->run_cancel_cause_,
             this->is_manual_run_, this->run_gallons_done_);
    this->record_gallons_delivery_(zid, std::max(0.0f, session_gal));
    const bool completed = false;
    this->stamp_finished_on_zone_off_ = completed;
    this->drive_zone(zid, false, completed);
    this->finish_attempt_(zid, now_epoch, completed);
    snprintf(this->last_run_outcome_, sizeof(this->last_run_outcome_), "cancelled_%s", this->run_cancel_cause_);
    this->set_phase_(EnginePhase::IDLE, "run_cancelled");
    this->is_manual_run_ = false;
    return;
  }

  if (this->run_gallons_done_ >= this->run_goal_gallons_) {
    ESP_LOGI(TAG, "run completed z=%d delivered=%.2f/%.2fg", zid, this->run_gallons_done_, this->run_goal_gallons_);
    this->record_gallons_delivery_(zid, std::max(0.0f, session_gal));
    this->stamp_finished_on_zone_off_ = true;
    this->drive_zone(zid, false, true);
    this->finish_attempt_(zid, now_epoch, true);
    strncpy(this->last_run_outcome_, "completed", sizeof(this->last_run_outcome_));
    this->set_phase_(EnginePhase::IDLE, "run_completed");
    this->is_manual_run_ = false;
    return;
  }
}

void IrrigationEngine::weekly_budget_evaluator(uint32_t now_epoch, bool ha_time_valid) {
  if (!this->config_.fallback_schedule_enabled || this->currently_on_zone_ != 0 || this->phase_ != EnginePhase::IDLE)
    return;
  if (!ha_time_valid || now_epoch == 0)
    return;

  time_t tt = static_cast<time_t>(now_epoch);
  struct tm lt {};
  localtime_r(&tt, &lt);
  const int hour = lt.tm_hour;
  const int minute = lt.tm_min;
  const int dow = (lt.tm_wday + 6) % 7;

  if (!in_watering_window(hour, minute, this->config_.global))
    return;
  if (is_blackout_day(dow, this->config_.global.blackout_weekday_bitmask))
    return;

  this->apply_week_reset_if_needed_(now_epoch, ha_time_valid);
  const bool feed_valid = this->ha_weekly_feed_valid_(now_epoch);
  const int picked = pick_next_zone_round_robin(this->config_, this->zones_, now_epoch, feed_valid, true);
  if (picked == 0)
    return;

  if (this->skip_next_run_pending_) {
    this->skip_next_run_pending_ = false;
    return;
  }

  this->any_alarm_latched_ = false;
  this->config_.last_served_zone_id = picked;
  this->run_zone_id_ = picked;
  this->is_manual_run_ = false;
  this->set_phase_(EnginePhase::PREFLIGHT_WAIT, "weekly_budget_pick");
  this->preflight_wait_left_ = 1;
  ESP_LOGI(TAG, "weekly budget picked zone %d epoch=%u tracking=%s", picked, now_epoch, this->tracking_source_);
}

bool IrrigationEngine::request_zone_on(int zone_id, uint32_t now_epoch) {
  ESP_LOGI(TAG, "zone_on request z=%d epoch=%u phase=%s on_zone=%d", zone_id, now_epoch, phase_name(this->phase_),
           this->currently_on_zone_);
  if (zone_id < 1 || zone_id > kNumZones) {
    ESP_LOGW(TAG, "zone_on(%d): rejected invalid zone_id", zone_id);
    return false;
  }
  if (this->phase_ != EnginePhase::IDLE || this->currently_on_zone_ != 0) {
    ESP_LOGW(TAG, "zone_on(%d): rejected busy phase=%s currently_on=%d", zone_id, phase_name(this->phase_),
             this->currently_on_zone_);
    return false;
  }
  if ((this->config_.zones_enabled_bitmask & (1 << (zone_id - 1))) == 0) {
    ESP_LOGW(TAG, "zone_on(%d): rejected not in enabled bitmask 0x%02x", zone_id, this->config_.zones_enabled_bitmask);
    return false;
  }
  if (!this->config_.zones[zone_id - 1].enabled) {
    ESP_LOGW(TAG, "zone_on(%d): rejected zone disabled in config", zone_id);
    return false;
  }

  this->any_alarm_latched_ = false;
  this->run_zone_id_ = zone_id;
  this->is_manual_run_ = true;
  this->set_phase_(EnginePhase::PREFLIGHT_WAIT, "manual_zone_on");
  this->preflight_wait_left_ = 1;
  ESP_LOGI(TAG, "zone_on(%d): accepted -> pre_flight", zone_id);
  return true;
}

bool IrrigationEngine::request_zone_off(int zone_id, uint32_t now_epoch) {
  ESP_LOGI(TAG, "zone_off request z=%d epoch=%u phase=%s on_zone=%d run_zone=%d", zone_id, now_epoch,
           phase_name(this->phase_), this->currently_on_zone_, this->run_zone_id_);
  if (zone_id < 1 || zone_id > kNumZones) {
    ESP_LOGW(TAG, "zone_off(%d): rejected invalid zone_id", zone_id);
    return false;
  }

  if (this->currently_on_zone_ == zone_id) {
    if (this->phase_ == EnginePhase::RUNNING) {
      const float session_gal = std::max(0.0f, this->integrate_run_gallons_());
      this->run_gallons_done_ = this->run_start_delivered_ + session_gal;
      this->sync_weekly_delivered_(zone_id, this->run_gallons_done_);
      this->record_gallons_delivery_(zone_id, session_gal);
      this->finish_attempt_(zone_id, now_epoch, false);
    }
    this->drive_zone(zone_id, false, false);
    if (this->phase_ != EnginePhase::IDLE) {
      strncpy(this->last_run_outcome_, "cancelled_manual", sizeof(this->last_run_outcome_));
      this->set_phase_(EnginePhase::IDLE, "manual_zone_off");
      this->is_manual_run_ = false;
      this->run_cancel_requested_ = false;
      ESP_LOGI(TAG, "zone_off(%d): stopped running zone", zone_id);
    }
    return true;
  }

  if (this->phase_ != EnginePhase::IDLE && this->run_zone_id_ == zone_id) {
    const char *prev_phase = phase_name(this->phase_);
    this->set_phase_(EnginePhase::IDLE, "manual_cancel_pending");
    this->is_manual_run_ = false;
    strncpy(this->last_run_outcome_, "cancelled_manual", sizeof(this->last_run_outcome_));
    ESP_LOGI(TAG, "zone_off(%d): cancelled pending run in phase=%s", zone_id, prev_phase);
    return true;
  }
  ESP_LOGW(TAG, "zone_off(%d): no matching active run", zone_id);
  return false;
}

bool IrrigationEngine::zone_actual_state(int zone_id) const {
  if (zone_id < 1 || zone_id > kNumZones)
    return false;
  return this->zones_[zone_id - 1].actual_state;
}

void IrrigationEngine::tick(uint32_t now_epoch, uint32_t now_ms, bool ha_time_valid) {
  const EnginePhase phase_before = this->phase_;
  this->tick_count_++;
  this->run_safety(now_epoch, now_ms);

  if (ha_time_valid && !this->last_ha_time_valid_) {
    ESP_LOGI(TAG, "HA time synced (epoch=%u), refreshing schedule plan", now_epoch);
    this->update_plan(now_epoch, ha_time_valid);
  }
  this->last_ha_time_valid_ = ha_time_valid;

  if (this->tick_count_ % 30 == 0)
    this->update_plan(now_epoch, ha_time_valid);
  if (this->tick_count_ % 60 == 0)
    this->weekly_budget_evaluator(now_epoch, ha_time_valid);

  if (this->phase_ != EnginePhase::IDLE)
    this->step_run(now_epoch, now_ms);

  if (this->phase_ != phase_before || (this->phase_ != EnginePhase::IDLE && this->tick_count_ % 10 == 0)) {
    ESP_LOGI(TAG,
             "tick #%u epoch=%u ha_time=%d phase=%s on_zone=%d run_z=%d manual=%d outcome=%s gpm=%.2f psi=%.1f",
             this->tick_count_, now_epoch, ha_time_valid, phase_name(this->phase_), this->currently_on_zone_,
             this->run_zone_id_, this->is_manual_run_, this->last_run_outcome_, this->read_flow_gpm(),
             this->read_pressure(this->currently_on_zone_ != 0));
  }
}

}  // namespace nedorachio
}  // namespace esphome
