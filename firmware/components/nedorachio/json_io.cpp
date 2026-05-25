#include "json_io.h"

#include <ArduinoJson.h>

namespace esphome {
namespace nedorachio {

namespace {

int weekday_bitmask_from_json(JsonArrayConst weekdays) {
  static const char *names[] = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"};
  int mask = 0;
  for (JsonVariantConst day : weekdays) {
    const char *d = day.as<const char *>();
    if (d == nullptr)
      continue;
    for (int i = 0; i < 7; i++) {
      if (strcasecmp(d, names[i]) == 0) {
        mask |= 1 << i;
        break;
      }
    }
  }
  return mask;
}

void parse_hhmm(const char *value, int &hour, int &minute) {
  hour = 0;
  minute = 0;
  if (value == nullptr)
    return;
  sscanf(value, "%d:%d", &hour, &minute);
}

}  // namespace

bool parse_config_json(const std::string &json, OperationalConfig &out) {
  JsonDocument doc;
  if (deserializeJson(doc, json) != DeserializationError::Ok)
    return false;
  if (!doc["global"].is<JsonObject>() || !doc["zones"].is<JsonObject>())
    return false;

  JsonObject g = doc["global"].as<JsonObject>();
  JsonObject ww = g["watering_window"].as<JsonObject>();
  parse_hhmm(ww["start"], out.global.schedule_start_hour, out.global.schedule_start_minute);
  parse_hhmm(ww["end"], out.global.schedule_end_hour, out.global.schedule_end_minute);
  out.global.blackout_weekday_bitmask = weekday_bitmask_from_json(g["blackout"]["weekdays"].as<JsonArrayConst>());
  out.global.attempt_cooldown_minutes = g["attempt_cooldown_minutes"] | 20.0f;
  out.global.max_attempt_minutes = g["max_attempt_minutes"] | g["maximum_runtime_minutes"] | 30.0f;
  out.global.no_flow_grace_s = g["no_flow_grace_seconds"] | 60.0f;
  out.global.no_flow_sustain_s = g["no_flow_sustain_seconds"] | 30.0f;
  out.global.rain_credit_mm_per_step = g["rain_credit_mm_per_step"] | 10.0f;
  out.global.rain_credit_gallons_per_zone_per_step = g["rain_credit_gallons_per_zone_per_step"] | 100.0f;
  out.global.rain_sensor_hold_hours_after_wet = g["rain_sensor_hold_hours_after_wet"] | 24.0f;

  out.zones_enabled_bitmask = 0;
  JsonObject zones = doc["zones"].as<JsonObject>();
  for (JsonPair kv : zones) {
    const int zid = atoi(kv.key().c_str());
    if (zid < 1 || zid > kNumZones)
      continue;
    JsonObject z = kv.value().as<JsonObject>();
    auto &zc = out.zones[zid - 1];
    zc.enabled = z["enabled"] | false;
    if (zc.enabled)
      out.zones_enabled_bitmask |= 1 << (zid - 1);
    zc.weekly_goal_gallons = z["weekly_goal_gallons"] | z["goal_gallons_per_cycle"] | 0.0f;
    zc.start_minimum_psi = z["start_minimum_psi"] | 35.0f;
    zc.start_maximum_psi = z["start_maximum_psi"] | 85.0f;
    zc.minimum_running_psi = z["minimum_running_psi"] | 20.0f;
    zc.minimum_running_psi_grace_seconds = z["minimum_running_psi_grace_seconds"] | 60.0f;
    zc.min_flow_gpm = z["minimum_flow_gpm"] | 0.2f;
    zc.max_flow_gpm = z["maximum_flow_gpm"] | 12.0f;
  }
  return true;
}

bool parse_runtime_json(const std::string &json, RuntimeState &out) {
  JsonDocument doc;
  if (deserializeJson(doc, json) != DeserializationError::Ok)
    return false;
  out.version = doc["version"] | doc["v"] | 2;
  out.updated_epoch = doc["updated_epoch"] | doc["u"] | 0;
  out.rain_sensor_last_wet_epoch = doc["rain_sensor_last_wet_epoch"] | doc["r"] | 0;
  out.rain_forecast_last_high_epoch = doc["rain_forecast_last_high_epoch"] | doc["f"] | 0;
  out.week_id_shadow = doc["week_id_shadow"] | doc["w"] | 0;
  out.last_served_zone_id = doc["last_served_zone_id"] | doc["s"] | 0;
  JsonObject zones = doc["zones"].is<JsonObject>() ? doc["zones"].as<JsonObject>() : doc["z"].as<JsonObject>();
  for (JsonPair kv : zones) {
    const int zid = atoi(kv.key().c_str());
    if (zid < 1 || zid > kNumZones)
      continue;
    JsonObject z = kv.value().as<JsonObject>();
    out.zones[zid - 1].last_finished_epoch = z["last_finished_epoch"] | z["lf"] | 0;
    out.zones[zid - 1].weekly_delivered_shadow =
        z["weekly_delivered_shadow"] | z["cycle_delivered_gallons"] | z["dg"] | 0.0f;
    out.zones[zid - 1].last_attempt_epoch = z["last_attempt_epoch"] | z["la"] | 0;
  }
  return true;
}

std::string serialize_runtime_json(const RuntimeState &state) {
  JsonDocument doc;
  doc["v"] = state.version;
  doc["u"] = state.updated_epoch;
  doc["r"] = state.rain_sensor_last_wet_epoch;
  doc["f"] = state.rain_forecast_last_high_epoch;
  doc["w"] = state.week_id_shadow;
  doc["s"] = state.last_served_zone_id;
  JsonObject zones = doc["z"].to<JsonObject>();
  for (int zid = 1; zid <= kNumZones; zid++) {
    JsonObject z = zones[String(zid)].to<JsonObject>();
    z["lf"] = state.zones[zid - 1].last_finished_epoch;
    z["ws"] = state.zones[zid - 1].weekly_delivered_shadow;
    z["la"] = state.zones[zid - 1].last_attempt_epoch;
  }
  std::string out;
  serializeJson(doc, out);
  return out;
}

}  // namespace nedorachio
}  // namespace esphome
