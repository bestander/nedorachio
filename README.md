# Nedorachio

Home-Assistant-controlled irrigation controller built on an 8-relay
ESP32-WROOM-32E development board running ESPHome. Replaces a Rachio.

All irrigation logic — schedules, cycle-and-soak, pre-flight gates, mid-run
cancels, retries, rain hold, catch-up, stats — lives on the device. Home
Assistant is a thin layer that pushes weather data, surfaces state, and routes
notifications.

See `docs/superpowers/specs/2026-04-30-nedorachio-irrigation-controller-design.md`
for the full design and `docs/superpowers/plans/` for the implementation plan.

---

## Hardware

### Bill of materials

- 8-channel ESP32-WROOM-32E relay/dev board (Amazon ASIN B0DK6QKNBM).
- Existing 24VAC sprinkler valve transformer (carried over from the Rachio).
- Hunter Mini-Clik (or equivalent normally-closed) rain sensor.
- EveryDropMeter Model 1004-EX flow meter (pulse output, reed switch).
- 0–100 PSI 0–5V pressure transducer with 24VDC IRM-05-24 supply
  (carried over from the existing ESP32-C3 device).
- 20kΩ + 10kΩ voltage divider for transducer signal → ADC.

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
| Rain sensor input  | GPIO27           | internal pull-up |
| Flow meter pulse   | GPIO26           | internal pull-up, interrupt-capable |
| Pressure ADC       | GPIO34           | ADC1, 11 dB attenuation |
| Status/select LED  | GPIO14           | active-high (flip `inverted:` if wiring is active-low) |
| Start/stop button  | GPIO21           | momentary, button-to-GND, internal pull-up |
| Zone-select button | GPIO22           | momentary, button-to-GND, internal pull-up |

### Calibration

Pressure transducer linear calibration is set in
`firmware/packages/03-sensors.yaml` under the `calibrate_linear:` filter.
The two voltages refer to the **divider midpoint** (≈ V_transducer / 3 with
the 20k+10k divider). Replace the placeholder points with two measured
voltage→PSI pairs from a known-good gauge.

`pulses_per_gallon` (HA `number.nedorachio_pulses_per_gallon`) defaults to
`10.0`. Calibrate during cutover by filling a 5-gallon bucket and dividing
counted pulses by 5.

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
    06-schedule.yaml         # weekly fire, skip, catch-up, plan readouts
    07-stats.yaml            # per-zone gallons, runs, durations + rollover
```

---

## Home Assistant setup

1. Copy `homeassistant/packages/nedorachio.yaml` into your HA config under
   `packages/`. Make sure `configuration.yaml` has
   `homeassistant: packages: !include_dir_named packages`.
2. Replace `weather.your_local_forecast` with your weather entity and
   `sensor.rain_observed_48h` with whatever observed-rain sensor you have.
   If you don't have one, the feeder writes `0` and the device falls back to
   the rain sensor and static-pressure gate.
3. Reload automations. Within 10 minutes, `number.nedorachio_rain_mm_last_48h`
   should be populated.
4. Edit the `notify.notify` line in `nedorachio_alarm_notify` to use your
   actual notification target (e.g. `notify.mobile_app_yourphone`).
5. Add a new dashboard view: Settings → Dashboards → Open the Lovelace
   dashboard → ⋮ → Edit Dashboard → ⋮ → Raw configuration editor → paste the
   contents of `homeassistant/packages/nedorachio_dashboard.yaml` under
   `views:`. Note that exact entity slugs depend on how HA names entities at
   discovery — confirm each card resolves before saving.

---

## Operation

### A normal day

- At `schedule_start_hour:schedule_start_minute` on enabled days, the device
  runs the pre-flight gate.
- If pre-flight passes, `run_full_cycle` iterates the zones in
  `zones_enabled_bitmask` (default: 1..4). Each zone runs through
  cycle-and-soak: `cycle_min` on, `soak_min` off, repeated until
  `total_min` accrues.
- Inter-zone transitions enforce `inter_zone_delay_seconds` (default 2s) to
  reduce water hammer.
- Per-zone stats accumulate to `zone_N_gallons_total`, `zone_N_run_count`,
  etc.; daily/monthly aggregates roll over at midnight.

### Manual run

- `button.nedorachio_run_now_zone_N` runs zone N once with its configured
  cycle/soak/total.
- `switch.nedorachio_zone_N` is the raw on/off control (still gated by the
  master enable / e-stop / single-zone invariant).
- `button.nedorachio_run_full_cycle` runs the full cycle on demand.

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

### Skipping a run

- `button.nedorachio_skip_next_run` consumes the *next* scheduled fire.
- `switch.nedorachio_fallback_schedule_enabled` disables the schedule
  indefinitely.

### Alarm reference

Every alarm latches `any_alarm_latched`, which blocks new pre-flights until
cleared via `button.nedorachio_clear_fault`.

| Alarm                            | Cause                                              | Action                  |
|----------------------------------|----------------------------------------------------|-------------------------|
| `alarm_pre_flight_failed`        | A pre-flight gate refused to start                 | Read `pre_flight_reason`; fix; clear fault. |
| `alarm_runtime_exceeded`         | A zone ran longer than `zone_N_max_runtime_min`    | Inspect for stuck relay; clear fault. |
| `alarm_no_flow`                  | gpm < `zone_N_min_flow_gpm` after `no_flow_grace_s` | Check pump/well/valve; retry or clear. |
| `alarm_high_flow`                | gpm > `zone_N_max_flow_gpm` for `high_flow_grace_s` | Check for broken pipe; clear fault. |
| `alarm_phantom_flow`             | gpm > `phantom_flow_gpm` while no zone on for 5+ min | Check valves; clear fault. |
| `alarm_low_pressure`             | PSI < `pressure_running_min_psi` mid-run for 5s+    | Inspect supply; retry or clear. |
| `alarm_high_pressure`            | PSI > `pressure_high_psi` for 10s+ during run       | Inspect; cancels run only when `high_pressure_cancels_run` is on. |

### Adding a fifth zone

1. Wire the valve to relay 5 (default GPIO17 — verify your board) and confirm
   the LED on the bench harness clicks when `switch.nedorachio_zone_5`
   toggles.
2. Set `zones_enabled_bitmask` to include bit 4. E.g. zones 1..5 enabled =
   `0b00011111 = 31`.
3. Tune `zone_5_total_min`, `zone_5_cycle_min`, `zone_5_soak_min`,
   `zone_5_min_flow_gpm`, `zone_5_max_flow_gpm`,
   `zone_5_max_runtime_min`.

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
