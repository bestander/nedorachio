# Nedorachio

Home-Assistant-controlled irrigation controller built on an 8-relay
ESP32-WROOM-32E development board running ESPHome. Replaces a Rachio.

All irrigation logic — per-zone cadence scheduler, cycle-and-soak, pre-flight
gates, mid-run cancels, retries, rain hold, stats — lives on the device. Home
Assistant is a thin layer that pushes weather data, surfaces state, and routes
notifications.

> **Note.** The original design doc in
> `docs/superpowers/specs/2026-04-30-nedorachio-irrigation-controller-design.md`
> describes a weekly day-of-week schedule with catch-up. That has been replaced
> by the per-zone cadence model documented below; treat the spec/plan docs as
> historical context for the engine and safety layer, not the scheduler.

---

## Hardware

### Bill of materials

- 8-channel ESP32-WROOM-32E relay/dev board (Amazon ASIN B0DK6QKNBM).
- Existing 24VAC sprinkler valve transformer (carried over from the Rachio).
- Hunter Mini-Clik (or equivalent normally-closed) rain sensor.
- EveryDropMeter Model 1004-EX flow meter (2-wire pulse + power interface).
- 0-100 PSI 0-5V pressure transducer (powered from shared 12V rail).
- Perfboard front-end parts:
  - Flow input network: 4N35, 1kΩ (Rpullup), 2.2kΩ (Rled), 10kΩ.
    - Note: EveryDrop's electrical spec recommends <= 1.8k source impedance at
      12V (1k preferred). This build uses 1k for Rpullup.
  - Pressure ADC divider: 10kΩ, 20kΩ (optional 100nF filter cap).

### GPIO map

The current firmware uses **placeholder** GPIO assignments. Verify with the
real board before flashing onto a system that's switching valves, then update
both the table below and the matching `pin:` lines in
`firmware/packages/02-zones.yaml` and `firmware/packages/03-sensors.yaml`.

| Function           | Placeholder GPIO | Notes |
|--------------------|------------------|-------|
| Relay 1 (zone 1)   | GPIO23           | active-low (`inverted: true`) |
| Relay 2 (zone 2)   | GPIO19           | active-low |
| Relay 3 (zone 3)   | GPIO18           | active-low |
| Relay 4 (zone 4)   | GPIO5            | active-low |
| Relay 5 (zone 5)   | GPIO17           | unused, terminal-blocked for future |
| Relay 6 (zone 6)   | GPIO16           | unused |
| Relay 7 (zone 7)   | GPIO4            | unused |
| Relay 8 (zone 8)   | GPIO13           | unused |
| Rain sensor input  | GPIO18           | internal pull-up |
| Flow meter pulse   | GPIO19           | external 10k pull-up to 3.3V |
| Pressure ADC       | GPIO34           | ADC1, 11 dB attenuation |
| Status/select LED  | GPIO14           | active-high (flip `inverted:` if wiring is active-low) |
| Start/stop button  | GPIO21           | momentary, button-to-GND, internal pull-up |
| Zone-select button | GPIO22           | momentary, button-to-GND, internal pull-up |

### Calibration

Pressure transducer linear calibration is set in
`firmware/packages/03-sensors.yaml` under the `calibrate_linear:` filter.
The voltages refer to the **divider midpoint** (V_adc ≈ 0.667 × V_transducer
with the 10k+20k divider used in this wiring).

Flow rate and total gallons are derived from pulse totals using calibrated
`pulses_per_gallon`.

Why not use the meter's nominal K/offset equation directly in firmware?
In this build, the meter signal is conditioned through a 4N35 isolation stage
and GPIO edge filtering. That end-to-end path changes the effective pulse
stream seen by ESPHome compared with the meter's ideal electrical model, so
field-calibrated `pulses_per_gallon` matches real delivered gallons more
reliably than nominal constants.

`pulses_per_gallon` (HA
`number.nedorachio_irrigation_controller_pulses_per_gallon`) defaults to
`344.4` from controlled-run calibration. Recalibrate in HA when hardware or
signal conditioning changes.

### Perfboard wiring schematic (shared 12V PSU + 4N35 flow isolation)

```text
PERFBOARD / SOLDER BOARD SCHEMATIC (12V PSU domain + ESP32 domain)

Create buses:
  NET +12V
  NET GND12
  NET +3V3
  NET GND_ESP

======================================================================
FLOW METER (EveryDrop 1004-EX, 2-wire) -> 4N35 -> ESP32 GPIO19
======================================================================

Define RED_NODE as this shared junction:
  Meter RED (+signal), Rpullup lower end, and 4N35 Pin2 (cathode).

+12V NET -- Rpullup 1k --------------------------> RED_NODE
+12V NET -- Rled 2.2k ----> 4N35 Pin1 (Anode)
RED_NODE ------------------> 4N35 Pin2 (Cathode)
Meter RED -----------------> RED_NODE
Meter BLACK (common) ----------------------------> GND12

4N35 Pin5 (Collector) ---------------------------> ESP32 GPIO19
GPIO19 -------------------- Rgpio 10k -----------> +3V3
4N35 Pin4 (Emitter) -----------------------------> GND_ESP

4N35-centered view (same wiring, pin-first):

                4N35 (DIP-6, top view)

            ┌─────────────────────────┐
 +12V--Rled----> Pin 1  Anode   Col 5 ├──────────> GPIO19 (ESP32 input)
 RED_NODE ------ Pin 2  Cathode        │
            (Pin 3 NC)                 │
 GND_ESP ------- Pin 4  Emitter  Base 6│ (NC)
            └─────────────────────────┘
                              |
                              +-- GPIO19 has 10k pull-up to 3.3V

RED_NODE wiring (explicit):
  +12V -- Rpullup 1k --+
                         +-- Meter RED
                         +-- 4N35 Pin2 (Cathode)

======================================================================
PRESSURE SENSOR (3-wire analog) -> ESP32 GPIO34 (ADC)
======================================================================

Pressure VCC ---------------------------------------> +12V NET
Pressure GND ---------------------------------------> GND12

Pressure OUT ---- R4 10k ---- PRESS_GPIO_NODE ------> ESP32 GPIO34
                                |
                                +---- R5 20k -------> GND_ESP
                                |
                                +---- C2 100nF ------> GND_ESP  [optional]

======================================================================
RAIN SENSOR + LOCAL BUTTONS + STATUS LED (ESP32 side)
======================================================================

RAIN SENSOR (normally-closed contact to GND when wet):
  GPIO18 -------------------------------> Rain sensor input
  GPIO18 uses ESP internal pull-up (`pullup: true` in firmware)
  Rain sensor other lead ---------------> GND_ESP

START/STOP BUTTON (momentary, normally-open):
  GPIO21 -------------------------------> One side of button
  GPIO21 uses ESP internal pull-up (`pullup: true`)
  Other side of button -----------------> GND_ESP

ZONE-SELECT BUTTON (momentary, normally-open):
  GPIO22 -------------------------------> One side of button
  GPIO22 uses ESP internal pull-up (`pullup: true`)
  Other side of button -----------------> GND_ESP

STATUS LED (active-high in current firmware):
  GPIO4 ---- Rled_status 330..1k ------> LED anode (+)
  LED cathode (-) ----------------------> GND_ESP
  (Set `inverted: true` in firmware if your LED wiring is active-low.)

======================================================================
POWER / REFERENCE (MANDATORY)
======================================================================

12V PSU + ------------------------------------------> +12V NET
12V PSU - ------------------------------------------> GND12

ESP32 3V3 ------------------------------------------> +3V3
ESP32 GND ------------------------------------------> GND_ESP

# Because pressure OUT is wired directly to ESP32 ADC,
# tie GND12 and GND_ESP together at one point (star ground).
GND12 ----------------------------------------------> GND_ESP
```

---

## Firmware

### First flash

```bash
cp firmware/secrets.yaml.example firmware/secrets.yaml
# Fill in WiFi credentials, generate API key + OTA password.
cd firmware
esphome run nedorachio.yaml      # USB; first time only
```

### Updating

After the first flash, OTA works:

```bash
cd firmware
esphome run nedorachio.yaml      # OTA, board stays installed
```

Counters and tunables persist across OTA reflashes via
`restore_value: true` globals.

### Layout

```
firmware/
  nedorachio.yaml            # entrypoint
  packages/
    01-core.yaml             # WiFi, API, OTA, time, status
    02-zones.yaml            # 8 relays + zone switches + safety
    03-sensors.yaml          # rain, flow, pressure
    04-tunables.yaml         # all number/switch entities
    05-engine.yaml           # pre-flight, cycle-and-soak, cancels, retry, sequencing
    06-schedule.yaml         # cadence evaluator, per-zone last-finished, skip, plan readouts
    07-stats.yaml            # per-zone gallons, runs, durations + rollover
```

---

## Home Assistant setup

1. Copy `homeassistant/packages/nedorachio.yaml` and
   `homeassistant/packages/nedorachio_config.yaml` into your HA config under
   `packages/`. Make sure `configuration.yaml` has
   `homeassistant: packages: !include_dir_named packages`.
2. Replace `weather.your_local_forecast` with your weather entity and
   `sensor.rain_observed_48h` with whatever observed-rain sensor you have.
   If you don't have one, the feeder writes `0` and the device falls back to
   the rain sensor and static-pressure gate.
3. Reload template entities and automations. On HA start (and every 30 minutes),
   `script.nedorachio_apply_config_profile` re-applies the config profile to the
   controller entities.
4. Within 10 minutes, `number.nedorachio_rain_mm_last_48h` should be populated.
5. Edit the `notify.notify` line in `nedorachio_alarm_notify` to use your
   actual notification target (e.g. `notify.mobile_app_yourphone`).
6. Add a new dashboard view: Settings → Dashboards → Open the Lovelace
   dashboard → ⋮ → Edit Dashboard → ⋮ → Raw configuration editor → paste the
   contents of `homeassistant/packages/nedorachio_dashboard.yaml` under
   `views:`. Note that exact entity slugs depend on how HA names entities at
   discovery — confirm each card resolves before saving.

---

## Operation

### Scheduling model

Per-zone cadence, not a weekly calendar. Each zone can run in one of two modes:

- **time target**: `zone_N_total_min` / `_cycle_min` / `_soak_min`
- **gallons target**: `zone_N_goal_gallons_per_cycle` / `_cycle_gallons` /
  `_soak_min` with carry-forward progress across attempts

A global watering window (`schedule_start_hour:minute` →
`schedule_end_hour:minute`, default `00:00 → 08:00`) gates *when* a zone may
start. End < start wraps midnight (e.g. `22:00 → 06:00`).

Every 60s the cadence evaluator picks the lowest-numbered enabled zone whose
cadence is due, runs pre-flight, and fires it. Zones run one at a time;
`sensor.next_due_zone` shows what's queued.

Automatic retries are not count-limited; instead, the evaluator waits global
`attempt_cooldown_minutes` between non-completed attempts.

### A normal day

- Inside the watering window, the evaluator finds zone *N* due (now ≥
  `zone_N_last_finished_epoch + zone_N_minimum_interval_hours·3600`).
- Pre-flight runs (master enable, time sync, rain sensor, rain forecast,
  static pressure, alarm-latch). If it fails, the cycle is aborted and the
  reason is logged.
- The runner executes either:
  - time target: cycle-and-soak until `total_min` accrues
  - gallons target: run until `cycle_gallons`, soak, repeat until
    `goal_gallons_per_cycle` is reached
- When the zone finishes (or is cancelled, or hits the runtime cap),
  `zone_N_last_finished_epoch` is stamped and persisted to NVS — the cadence
  resets from that point.
- The next evaluator tick picks the next eligible zone, if any. If the window
  closes mid-run, the in-progress zone finishes; no new zone starts until the
  window reopens.
- Per-zone stats accumulate to `zone_N_gallons_total`, `zone_N_run_count`,
  etc.; daily/monthly aggregates roll over at midnight.

### Manual run

- `switch.nedorachio_zone_N` is the raw on/off control (still gated by the
  master enable / e-stop / single-zone invariant). Toggling off stamps
  `last_finished_epoch` and therefore resets cadence timing.

### Local controls

Two physical buttons and an LED on the device:

- **Status / select LED.** Solid ON while any zone is watering. After a
  zone-select press, blinks N times (where N is the newly selected zone),
  then returns to its idle state. Off when nothing is happening.
- **Start/stop button.** Press once while idle → starts the currently
  selected zone. The total runtime is capped by `local_button_max_min`
  (default 30 min, HA-tunable as `number.nedorachio_manual_run_max_minutes`)
  so an unattended press can't run forever. Press again (or press during
  *any* run, including a scheduled full cycle) → cancels everything with
  cause `user`. The cancellation does not retry.
- **Zone-select button.** Cycles through enabled zones (per
  `zones_enabled_bitmask`), wrapping at 8. The LED then blinks the new
  selection's number so you can confirm without looking at HA. Selection
  persists across reboots.

### Schedule enable/disable

- `switch.nedorachio_fallback_schedule_enabled` disables the cadence evaluator
  indefinitely. Manual per-zone switch control remains available.

### Clock survival across power loss

The fallback clock uses the highest of:

- `last_known_epoch` — written to NVS once an hour while HA time is valid.
- `fallback_start_epoch_est` — hardcoded baseline (2026-06-01 11:00 EST), used
  only on the very first boot before any HA sync.

On a brand-new device with no persisted cadence history, each
`zone_N_last_finished_epoch` initializes to this same baseline. That means the
controller behaves as if each zone was last watered at the default baseline
time, rather than treating missing history as "due immediately."

After a reboot without WiFi, the clock resumes within ~1h of reality, so
cadence checks (which are in hours) stay correct. Per-zone
`last_finished_epoch` is also NVS-persisted, so a watering that completed
just before a power loss isn't forgotten.

### Alarm reference

Every alarm latches `any_alarm_latched`, which blocks new pre-flights until
cleared through maintenance actions (for example, local controls or an
advanced/internal service call).

| Alarm                            | Cause                                              | Action                  |
|----------------------------------|----------------------------------------------------|-------------------------|
| `alarm_pre_flight_failed`        | A pre-flight gate refused to start                 | Read `pre_flight_reason`; fix; clear fault. |
| `alarm_runtime_exceeded`         | A zone ran longer than `maximum_runtime_minutes`    | Inspect for stuck relay; clear fault. |
| `alarm_no_flow`                  | gpm < `zone_N_minimum_flow_gpm` after `no_flow_grace_s` | Check pump/well/valve; retry or clear. |
| `alarm_high_flow`                | gpm > `zone_N_maximum_flow_gpm` for `high_flow_grace_s` | Check for broken pipe; clear fault. |
| `alarm_phantom_flow`             | gpm > `phantom_flow_gpm` while no zone on for 5+ min | Check valves; clear fault. |
| `alarm_low_pressure`             | PSI < `pressure_running_min_psi` mid-run for 5s+    | Inspect supply; retry or clear. |
| `alarm_high_pressure`            | PSI > `pressure_high_psi` for 10s+ during run       | Inspect; cancels run only when `high_pressure_cancels_run` is on. |

### Adding a fifth zone

1. Wire the valve to relay 5 (default GPIO17 — verify your board) and confirm
   the LED on the bench harness clicks when `switch.nedorachio_zone_5`
   toggles.
2. Set `zones_enabled_bitmask` to include bit 4. E.g. zones 1..5 enabled =
   `0b00011111 = 31`.
3. Tune `zone_5_total_minutes`, `zone_5_cycle_minutes`, `zone_5_soak_minutes`,
   `zone_5_minimum_interval_hours`, `zone_5_minimum_flow_gpm`, `zone_5_maximum_flow_gpm`.
   Runtime safety is controlled globally via `maximum_runtime_minutes`.

---

## Troubleshooting

- **Device offline in HA.** Check `binary_sensor.nedorachio_controller_online`.
  WiFi may have dropped; the device will fall back to its AP `nedorachio
  fallback` if it can't reach the configured SSID.
- **Pre-flight failing forever.** Check `text_sensor.nedorachio_last_run_outcome`
  or grep ESPHome logs for `preflight FAIL: <reason>`. Common causes:
  `master_enable_off`, `time_not_synced` (waiting for NTP),
  `pressure_too_low` (static PSI below threshold).
- **`rain_mm_last_48h` keeps blocking even after rain stopped.** The device
  enforces `rain_hold_hours_after_forecast` after the last over-threshold
  push; lower it or push `0` manually via Dev-Tools → Services →
  `number.set_value`.
- **HA went offline → device thinks it's still raining.** TTL kicks in:
  `rain_mm_last_48h` is treated as `0` if it hasn't been pushed within
  `rain_mm_max_age_hours` (default 12h).
- **Counter stuck after OTA.** Globals with `restore_value: true` survive OTA;
  if a counter resets unexpectedly, check the ESPHome change log for
  flash-layout changes — cross-version flash can invalidate stored values.
