#pragma once

#include "models.h"

#include <string>

namespace esphome {
namespace nedorachio {

bool parse_config_json(const std::string &json, OperationalConfig &out);
bool parse_runtime_json(const std::string &json, RuntimeState &out);
std::string serialize_runtime_json(const RuntimeState &state);

}  // namespace nedorachio
}  // namespace esphome
