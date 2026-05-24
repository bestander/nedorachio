import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import binary_sensor, output, sensor
from esphome.const import CONF_ID, CONF_TIME_ID

CODEOWNERS = ["@nedorachio"]
DEPENDENCIES = ["time"]
AUTO_LOAD = ["sensor", "binary_sensor", "output"]

time_ns = cg.esphome_ns.namespace("time")
RealTimeClock = time_ns.class_("RealTimeClock")

CONF_PRESSURE_ID = "pressure_sensor_id"
CONF_FLOW_GPM_ID = "flow_gpm_sensor_id"
CONF_FLOW_TOTAL_ID = "flow_total_sensor_id"
CONF_RAIN_ID = "rain_sensor_id"
CONF_ZONE_OUTPUTS = "zone_outputs"
CONF_CONFIG_PROFILE = "config_profile"

nedorachio_ns = cg.esphome_ns.namespace("nedorachio")
NedorachioComponent = nedorachio_ns.class_("NedorachioComponent", cg.Component)
IrrigationEngine = nedorachio_ns.class_("IrrigationEngine")

CONFIG_SCHEMA = (
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(NedorachioComponent),
            cv.GenerateID("engine_id"): cv.declare_id(IrrigationEngine),
            cv.Required(CONF_TIME_ID): cv.use_id(RealTimeClock),
            cv.Required(CONF_PRESSURE_ID): cv.use_id(sensor.Sensor),
            cv.Required(CONF_FLOW_GPM_ID): cv.use_id(sensor.Sensor),
            cv.Required(CONF_FLOW_TOTAL_ID): cv.use_id(sensor.Sensor),
            cv.Required(CONF_RAIN_ID): cv.use_id(binary_sensor.BinarySensor),
            cv.Required(CONF_ZONE_OUTPUTS): cv.ensure_list(cv.use_id(output.BinaryOutput)),
            cv.Required(CONF_CONFIG_PROFILE): cv.string,
        }
    )
    .extend(cv.COMPONENT_SCHEMA)
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    engine = cg.new_Pvariable(config["engine_id"])
    await cg.register_component(var, config)

    time_var = await cg.get_variable(config[CONF_TIME_ID])
    cg.add(var.set_time(time_var))
    cg.add(var.set_engine(engine))
    cg.add(var.set_config_profile(config[CONF_CONFIG_PROFILE]))

    outputs = []
    for i, out_id in enumerate(config[CONF_ZONE_OUTPUTS]):
        out = await cg.get_variable(out_id)
        cg.add(engine.set_zone_output(i, out))

    pressure = await cg.get_variable(config[CONF_PRESSURE_ID])
    flow_gpm = await cg.get_variable(config[CONF_FLOW_GPM_ID])
    flow_total = await cg.get_variable(config[CONF_FLOW_TOTAL_ID])
    rain = await cg.get_variable(config[CONF_RAIN_ID])
    cg.add(engine.set_sensors(pressure, flow_gpm, flow_total, rain))
