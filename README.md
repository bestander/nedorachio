# Nedorachio

ESP32-WROOM-32E irrigation controller (ESPHome) replacing a Rachio. Eight relay
outputs, flow meter, pressure transducer, and rain sensor.

**Scheduling runs on the device.** Home Assistant tracks calendar-week progress,
feeds weather rain, stores last-watering times, and shows the dashboard.

Design spec: `docs/superpowers/specs/2026-05-25-weekly-gallons-scheduler-design.md`

---

## How it works

Each enabled zone has a **weekly gallon goal**. During the configured watering
window, the controller round-robins among zones that still need water, capping
each attempt at `max_attempt_minutes` and applying a per-zone cooldown after
every attempt.

**Weekly progress (HA-primary):**

```
weekly_delivered = zone_gallons_lifetime − week_baseline_gallons
```

Baselines reset every **Monday 00:00** (configured timezone). Gallons lifetime is
persisted in HA (`input_number.nedorachio_zone_N_gallons_lifetime`) and synced
monotonically from the controller, so weekly progress survives a reflash even when
ESP flash storage is empty. Last-known weekly delivered is also persisted
(`input_number.nedorachio_zone_N_weekly_delivered_last`) to recover when lifetime
has not synced yet. The ESP reads `sensor.nedorachio_zone_N_weekly_delivered`
when online; the controller ignores decreases (including spurious 0 on reconnect).

**Rain credit:** observed rain this week reduces each zone's effective target
(linear ratio, globally configurable — default **10 mm → 100 gal**). The physical
rain sensor still blocks runs while wet.

**Config is flash-only** — edit `firmware/packages/11-config-profile.yaml` and
reflash. The dashboard reads weekly goals and rain-credit ratio from device
sensors (`sensor.nedorachio_irrigation_controller_zone_N_weekly_goal_gallons`).

---

## Hardware

Enclosure CAD: [Onshape model](https://cad.onshape.com/documents/9c8fb8a497eae65202519f86/w/afbdc8fc01fccc1892897bd4/e/039000a7d5b9f349569a7599?renderMode=0&uiState=6a022ed0ac288eec0668bda4)

| Item | Notes |
|------|-------|
| 8-ch ESP32 relay board | Amazon B0DK6QKNBM |
| 24VAC transformer | From existing Rachio install |
| Hunter Mini-Clik | NC rain sensor |
| EveryDrop 1004-EX | Pulse flow meter |
| 0–100 PSI transducer | 0–5 V, 12 V powered |

GPIO assignments are in `firmware/packages/10-nedorachio-component.yaml` — verify
against your board before switching live valves.

| Function | GPIO |
|----------|------|
| Relays 1–8 | 32, 33, 25, 26, 27, 14, 12, 13 |
| Rain sensor | 18 (pull-up) |
| Flow pulse | 19 (10k pull-up to 3.3 V) |
| Pressure ADC | 34 |

Flow uses field-calibrated `pulses_per_gallon` (default 344.4) in the C++
component. Pressure linear calibration is in the ESPHome sensor filters.

<details>
<summary>Perfboard wiring schematic</summary>

```text
FLOW (12V domain → 4N35 → GPIO19):
  +12V -- 1k -- RED_NODE -- meter RED
  +12V -- 2.2k -- 4N35 anode
  RED_NODE -- 4N35 cathode
  meter BLACK -- GND12
  4N35 collector -- GPIO19; 10k pull-up GPIO19→3.3V; emitter -- GND_ESP

PRESSURE (GPIO34 ADC):
  sensor VCC→+12V, GND→GND12
  OUT -- 10k -- GPIO34 -- 20k -- GND_ESP (optional 100nF to GND_ESP)

RAIN: GPIO18 (internal pull-up) -- sensor -- GND_ESP
Star-ground GND12 and GND_ESP at one point (pressure OUT ties to ESP ADC).
```

</details>

---

## Firmware

```bash
cp firmware/secrets.yaml.example firmware/secrets.yaml   # WiFi, API key, OTA password
cd firmware
esphome run nedorachio.yaml    # USB first flash; OTA thereafter
```

```
firmware/
  nedorachio.yaml
  components/nedorachio/       # C++ scheduler + engine
  packages/
    10-nedorachio-component.yaml
    11-config-profile.yaml     # ← edit schedule config here
src/nedorachio/                # Python reference (pytest)
```

### Config profile (`11-config-profile.yaml`)

Key global fields:

| Field | Purpose |
|-------|---------|
| `watering_window` | When scheduled runs may start (wraps midnight) |
| `blackout.weekdays` | Days with no scheduled runs |
| `max_attempt_minutes` | Cap per watering attempt |
| `attempt_cooldown_minutes` | Wait after every attempt |
| `rain_credit_mm_per_step` | mm of rain per credit step (default 10) |
| `rain_credit_gallons_per_zone_per_step` | gal credit per zone per step (default 100) |
| `rain_sensor_hold_hours_after_wet` | Block after rain sensor dries |

Per zone: `enabled`, `weekly_goal_gallons`, pressure/flow limits.

---

## Home Assistant

1. Copy `homeassistant/packages/nedorachio.yaml` and `nedorachio-dashboard.yaml`
   into your HA `packages/` folder (`homeassistant: packages: !include_dir_named packages`).
2. Install [OpenWeatherMap](https://www.home-assistant.io/integrations/openweathermap/)
   for `sensor.openweathermap_rain_intensity` (package falls back to
   `sensor.openweathermap_rain`).
3. Reload template entities and automations.
4. Edit the `notify.notify` target in `nedorachio_alarm_notify`.
5. **First deploy mid-week:** set each zone's `week_baseline_gallons` and
   `rain_week_baseline_mm` to current lifetime totals (or wait for Monday reset).
6. **After upgrading from device-only lifetime tracking:** if weekly progress was
   reset by a reflash, set each `input_number.nedorachio_zone_N_gallons_lifetime`
   to `week_baseline_gallons + delivered_this_week` (or wait for the sync
   automation once the controller reports totals again).

| Entity | Role |
|--------|------|
| `input_text.nedorachio_zone_N_last_watering` | Last run epoch (ESP reads/writes) |
| `input_number.nedorachio_zone_N_gallons_lifetime` | HA-persisted total gallons (survives reflash) |
| `input_number.nedorachio_zone_N_week_baseline_gallons` | Lifetime at start of calendar week |
| `sensor.nedorachio_zone_N_weekly_delivered` | Gallons this calendar week |
| `sensor.nedorachio_rain_observed_week` | Rain mm this calendar week |
| `sensor.nedorachio_rain_credit_gallons_per_zone` | Gallon credit from rain |
| `number.nedorachio_irrigation_controller_rain_mm_this_week` | Pushed to ESP every 10 min |
| `switch.nedorachio_irrigation_controller_fallback_schedule_enabled` | Master schedule on/off |

Dashboard (`nedorachio-dashboard.yaml`) shows four active zones by default — edit
names and zone list to match your wiring.

---

## Operation

1. **Evaluator** (every 60 s, idle): inside watering window, not blackout, pick
   next eligible zone with a weekly deficit (after rain credit).
2. **Pre-flight:** master enable, time sync, rain sensor (+ hold), static
   pressure, no latched alarm.
3. **Run:** deliver gallons until effective weekly target or attempt cap; partial
   delivery counts; rain sensor wet cancels mid-run.
4. **Cooldown:** per-zone wait before that zone is eligible again.

Manual zone switches bypass the scheduler and weekly gallon quota (you can run a zone
after its weekly goal is met); they still respect safety gates (rain, no-flow, attempt cap).
Local start/stop and zone-select buttons work as described in the firmware YAML.

---

## Troubleshooting

| Symptom | Check |
|---------|-------|
| Nothing schedules | `fallback_schedule_enabled`, watering window, blackout days, weekly goals met |
| Pre-flight skip | ESPHome logs / `text.nedorachio_irrigation_controller_last_run_outcome` |
| Weekly remaining wrong | HA goal `input_number` mirrors flash config; rain credit sensors |
| Rain credit not applied | `rain_lifetime_mm` / `rain_week_baseline_mm`; ESP `rain_mm_this_week` stale after 12 h TTL |
| ESP rain warnings at boot | Reload HA templates before ESP connects |

---

## Tests

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest tests/ -q
```
