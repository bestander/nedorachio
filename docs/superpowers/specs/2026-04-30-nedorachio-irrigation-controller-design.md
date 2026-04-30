# Nedorachio — Home Assistant Irrigation Controller

**Date:** 2026-04-30
**Status:** Approved design, ready for implementation plan
**Owner:** bestander

## 1. Summary

Replace a Rachio sprinkler controller with an off-the-shelf 8-relay ESP32-WROOM-32E development board running ESPHome, fully controlled by Home Assistant. Consolidate the existing standalone ESP32-C3 pressure-sensor device onto the same board. Add a rain sensor (binary input), a flow meter (pulse input, EveryDropMeter 1004-EX), and the migrated pressure transducer (ADC input). The device exposes zone relays as Home Assistant switches, runs a small autonomous fallback schedule when the network is up but Home Assistant isn't, and lets Home Assistant own all richer scheduling, sensor-driven decisions, and alarms.

## 2. Goals

- Replace Rachio for 4 active sprinkler zones (controller has headroom for up to 8).
- Expose every relay as an HA switch over the encrypted ESPHome native API.
- Prevent water hammer with on-device interlocks: at most one zone on at a time, plus a configurable inter-zone close→open delay.
- Run a simple autonomous schedule on the device (sequential zones with cycle-and-soak) so a Home Assistant outage does not stop watering.
- Let Home Assistant cancel and replace any planned run with its own (richer) cycle-and-soak script driven by sensors and weather forecast.
- Integrate a rain sensor, a pulse flow meter, and the existing pressure transducer; expose all of them to Home Assistant; alarm and shut off on the failure modes described in §6.
- Keep the configuration version-controlled: ESPHome firmware YAML and the Home Assistant package YAML both live in this repo.

## 3. Non-goals

- ET-based or weather-driven watering-minute calculation (Smart Irrigation HACS integration). Listed in roadmap; can be layered on top later.
- Surviving full power loss with persisted runtime state. Time source is NTP-only; on cold boot the device waits for NTP before arming the fallback schedule.
- HACS dependencies (no `scheduler-component`, no Smart Irrigation, no custom cards). Vanilla Home Assistant only.
- Master valve or pump-start support. Not needed for this site.
- Multi-controller installations or remote (cellular) connectivity.

## 4. Context

- **Existing controller:** Rachio, 4 active zones, 24VAC solenoids, no master valve or pump start. Stock 24VAC transformer in place.
- **Existing pressure sensor:** ESP32-C3-WROOM-02-N4 + IRM-05-24 AC-DC supply + G1/4 0–100 PSI / 0–5V transducer with a 20kΩ + 10kΩ voltage divider feeding the ADC. Firmware is ESPHome with linear PSI calibration. Source: `github.com/bestander/esp32-110v-pressure-sensor`. This device retires when consolidation is complete.
- **New controller hardware:** 8-relay AC/DC ESP32-WROOM-32E development board (Amazon ASIN B0DK6QKNBM). Onboard 110V AC → low-voltage supply powers the ESP32 and relay coils. Relays are dry contact, suitable for switching one leg of 24VAC to each zone valve.
- **Home Assistant:** already in use; pressure sensor is already integrated via the ESPHome native API. Same integration path will be used for the new device.

## 5. Architecture

### 5.1 Component diagram (logical)

```
                              ┌────────────────────────────────────────┐
                              │            Home Assistant              │
                              │  ─ helpers (input_*)                   │
                              │  ─ scripts (cycle-and-soak, run zone)  │
                              │  ─ automations (rain, flow, pressure,  │
                              │     schedule fire, takeover handshake) │
                              │  ─ dashboard (Lovelace view)           │
                              │  ─ utility_meter for per-zone gallons  │
                              └──────────────┬─────────────────────────┘
                                             │ ESPHome native API (TLS, encrypted)
                              ┌──────────────┴─────────────────────────┐
                              │       Nedorachio controller            │
                              │       (ESP32-WROOM-32E, ESPHome)       │
                              │  ─ 8 zone relays                       │
                              │  ─ Rain binary_sensor                  │
                              │  ─ Flow pulse counter + GPM            │
                              │  ─ Pressure ADC + linear calibration   │
                              │  ─ Safety: 1-zone-max, inter-zone gap, │
                              │     per-zone runtime cap, HA watchdog  │
                              │  ─ Fallback weekly schedule with C&S   │
                              │  ─ Tunable parameters as HA entities   │
                              └──────────────┬─────────────────────────┘
                                             │
                          ┌──────────┬───────┼───────┬─────────────┐
                          │          │       │       │             │
                       Zone 1     Zone 4   Rain    Flow         Pressure
                       (24VAC)…   (24VAC)  switch  meter pulse  transducer (0–5V)
```

### 5.2 Responsibility split

- **On the device (firmware):** relay control, sensor reading and calibration, safety interlocks, fallback weekly schedule with cycle-and-soak, exposing every parameter as a Home Assistant entity for tuning.
- **In Home Assistant:** rich scheduling, sensor-driven overrides (weather forecast, pressure thresholds, flow alarms), per-zone water totals, dashboards, notifications, persisted long-term history.

The device must remain useful when Home Assistant is unreachable; Home Assistant must be able to cancel and replace any planned device-side run when present.

## 6. Hardware and wiring

### 6.1 Bill of materials

- 1 × 8-relay AC/DC ESP32-WROOM-32E development board (B0DK6QKNBM).
- Reused 24VAC sprinkler transformer (existing).
- Reused 0–100 PSI / 0–5V pressure transducer + 20kΩ + 10kΩ voltage divider + IRM-05-24 24VDC supply (existing, migrated from the C3 device).
- 1 × rain sensor (normally-closed type, e.g. Hunter Mini-Clik or equivalent). Polarity is configurable in firmware.
- 1 × EveryDropMeter Model 1004-EX flow meter (reed-switch pulse output).
- New weather-rated enclosure sized for the board, the 24VAC transformer (or pigtail), the 24VDC transducer supply, and terminal blocks for zones, rain, flow, and pressure.

### 6.2 Wiring

- **Zone valves (×4):** 24VAC transformer hot to each relay COM; relay NO to that zone's valve hot; valve common to 24VAC common. Free relay channels are wired to terminal blocks for future expansion.
- **Rain sensor:** one wire to a digital input GPIO (internal pull-up enabled), other wire to GND. Wet → contacts open (or close, depending on model) → polarity configured in firmware.
- **Flow meter:** signal wire to an interrupt-capable digital input GPIO (internal pull-up), other wire to GND. Software debounce in ESPHome (`pulse_counter` or `pulse_meter` platform with internal filter); add an external RC if false counts are observed.
- **Pressure transducer:** 24VDC supply → transducer; signal (0–5V) → 20kΩ + 10kΩ divider → ADC1 input GPIO (must be ADC1 because ADC2 is unusable when WiFi is on). Reuse the C3 device's calibration points verbatim.

### 6.3 GPIO map

The 8-relay board's relay-channel GPIO assignment is fixed by board layout and is not published in the listing description. The exact map (and the chosen free GPIOs for rain, flow, and pressure ADC) is captured during the implementation plan, on the bench, before the firmware is written. Constraints to honor:

- Pressure ADC must use a pin on ADC1 (GPIO32–39).
- Flow input must be on an interrupt-capable GPIO with no boot-state conflict (avoid GPIO0/2/12/15 as inputs unless their boot-strap behavior is verified).
- Rain input has no special constraint beyond pull-up support.

## 7. Firmware (ESPHome)

### 7.1 Entities exposed to Home Assistant

- `switch.zone_1` … `switch.zone_8` — one per relay. Turning one ON in HA goes through the safety layer; the relay is not driven directly by the switch state.
- `binary_sensor.rain_sensor` — current wet/dry, polarity configurable.
- `sensor.flow_pulses_total` — monotonically increasing pulse count. HA derives gallons.
- `sensor.flow_rate_gpm` — instantaneous flow rate, smoothed.
- `sensor.pressure_psi` — calibrated PSI, linear from voltage divider.
- `binary_sensor.controller_online` — heartbeat / availability for HA automations.
- `binary_sensor.time_synced` — true once NTP has synced at least once since boot.
- `binary_sensor.zone_runtime_exceeded` — pulses true when a per-zone runtime cap is hit.
- `button.emergency_stop_all` — forces every zone off immediately.
- `button.skip_next_run` — vetoes only the next fallback fire (persisted in flash).
- `button.run_now` — kicks off the on-device cycle-and-soak full cycle immediately.
- `switch.fallback_schedule_enabled` — global enable for the on-device schedule.
- `sensor.next_planned_run` — timestamp of the next planned fallback fire.

### 7.2 Tunable parameters (exposed as `number` / `select` / `time` entities)

Persisted in flash, editable from HA without re-flashing:

- Per zone: total minutes, cycle minutes, soak minutes, hard maximum runtime cap.
- Pulses-per-gallon for the flow meter.
- Pressure calibration points (voltage → PSI linear interpolation).
- Inter-zone close→open delay (default 2s).
- HA-watchdog timeout (default 120s).
- Fallback schedule: days-of-week mask (default Tue + Sat), start time (default 06:00 local).

### 7.3 Safety invariants (enforced in firmware regardless of HA state)

1. **At most one zone ON at a time.** A request to turn zone N on while zone M is on first turns M off and waits the inter-zone delay before turning N on.
2. **Inter-zone close→open delay.** Default 2s, configurable. Ensures pressure settles before the next valve opens.
3. **Per-zone hard maximum runtime.** Default 60 minutes, configurable. On exceedance, the zone is shut off and `binary_sensor.zone_runtime_exceeded` fires.
4. **HA heartbeat watchdog.** If the native API connection has been down for longer than the watchdog timeout while any zone is ON, all zones are shut off.
5. **Boot-safe state.** On boot, every relay is forced OFF before WiFi/HA connect. No glitch can leave a zone ON across boot.

These invariants apply to *every* path that drives a zone — the HA switch, the on-device fallback schedule, and the `run_now` button.

### 7.4 Fallback weekly schedule

- Triggered by ESPHome's `time:` cron action on the configured days/start time.
- Pre-fire guard: if any zone switch is currently ON, the fallback aborts (HA is already watering). This avoids HA-vs-fallback collisions.
- Pre-fire guard: if `binary_sensor.time_synced` has never been true since boot, the fallback does not fire.
- Pre-fire guard: if `switch.fallback_schedule_enabled` is OFF, the fallback does not fire.
- Pre-fire guard: if `button.skip_next_run` was pressed since the last fire, the fallback consumes the skip and does not fire this time.
- On fire: iterate enabled zones in order; for each, run cycle-and-soak (run for `cycle_min`, off for `soak_min`, repeat until `total_min` accumulated), honoring the inter-zone delay between zones.
- Each iteration re-checks the master enable and emergency-stop state; either flipping mid-run aborts the remaining iterations cleanly.

### 7.5 Time source

- NTP via WiFi only.
- ESPHome's free-running clock holds across transient WiFi loss; on cold boot the device waits for first NTP sync before arming the fallback (`binary_sensor.time_synced` gates it).
- Power loss is explicitly out of scope: when it happens, the device boots, waits for NTP, then resumes — no fallback fires until time is known.

## 8. Home Assistant configuration

The HA configuration ships as a single Home Assistant Package YAML so it is version-controlled with the firmware.

### 8.1 Helpers (`input_*`)

- Per zone (×4 active): `input_number.zone_N_duration_min`, `input_boolean.zone_N_enabled`, `input_text.zone_N_name`, `input_number.zone_N_cycle_min`, `input_number.zone_N_soak_min`, `input_number.zone_N_min_flow_gpm`, `input_number.zone_N_max_flow_gpm`.
- Rain hold: `input_number.rain_hold_hours` (default 12), `input_datetime.rain_last_wet_at` (auto-stamped).
- Phantom flow threshold: `input_number.phantom_flow_gpm` (default 0.5).
- Pressure thresholds: `input_number.pressure_low_running_psi`, `input_number.pressure_high_psi`, `input_boolean.high_pressure_cancels_run` (default off — notify-only).
- Forecast skip: `input_number.forecast_rain_skip_mm` (default 5), `input_number.forecast_window_hours` (default 12).
- Master switches: `input_boolean.irrigation_master_enable`, `input_boolean.let_ha_take_over`.

### 8.2 Scripts

- `script.run_zone_with_cycle_soak(zone_id, total_min, cycle_min, soak_min)` — looped on/off until total accumulated. Honors `irrigation_master_enable` and rain hold each iteration; aborts cleanly on rain trip.
- `script.run_full_cycle` — runs `run_zone_with_cycle_soak` for each enabled zone in sequence. The HA-side equivalent of a fallback run, woven with sensor checks.
- `script.cancel_all` — turns every zone off immediately; called by alarms and the "stop" dashboard button.

### 8.3 Automations

- **Rain wet:** rain sensor goes wet → `script.cancel_all`, stamp `rain_last_wet_at`. (Q7 leg B.)
- **Schedule fire (HA-driven schedules):** at each user-defined fire time → check `irrigation_master_enable`; rain hold (`now - rain_last_wet_at >= rain_hold_hours`); forecast skip (built-in HA weather entity says > `forecast_rain_skip_mm` of rain in the next `forecast_window_hours`); pressure within bounds → either call `script.run_full_cycle` or skip. (Q7 leg A.)
- **HA-takeover handshake:** when `let_ha_take_over` flips ON → press `button.skip_next_run` on the device; when OFF → no action (fallback runs normally).
- **No-flow alarm:** zone ON for > 60s with `flow_rate_gpm < zone_N_min_flow_gpm` → `script.cancel_all`, notify.
- **High-flow alarm:** `flow_rate_gpm > zone_N_max_flow_gpm` for > 30s → `script.cancel_all`, notify.
- **Phantom-flow alarm:** no zone ON, `flow_rate_gpm > phantom_flow_gpm` for > 5 minutes → notify (no zone to cancel).
- **Low-pressure during run:** zone ON for > 30s, `pressure_psi < pressure_low_running_psi` → `script.cancel_all`, notify.
- **High-pressure:** `pressure_psi > pressure_high_psi` for > 10s → notify; if `high_pressure_cancels_run` is on, also `script.cancel_all`.
- **Per-zone gallon totals:** `utility_meter` integration sourced from `flow_pulses_total`, gated by which zone is active. Daily and monthly cycles per zone.

### 8.4 Dashboard

A single Lovelace view, vanilla cards only:

- Master toggle, "let HA take over" toggle, emergency stop button.
- Last-run summary (which zones ran, gallons, duration).
- Next planned run (from `sensor.next_planned_run`).
- Per-zone tile: enabled toggle, duration spinner, "run now" button, last-run gallons.
- Live tiles: rain wet/dry, flow GPM, pressure PSI.
- Recent alarms feed.

## 9. Failure modes and mitigations

| Failure | Detection | Mitigation |
|---|---|---|
| Stuck HA automation leaves zone on | Per-zone runtime cap (firmware) | Zone shut off, `zone_runtime_exceeded` fires |
| HA crashes mid-run | Native API watchdog (firmware) | All zones shut off after watchdog timeout |
| WiFi outage mid-run | Same as above (HA unreachable from device) | Same as above; fallback runs at next scheduled day |
| Two zones commanded on simultaneously | Single-zone invariant (firmware) | Earlier zone closed, inter-zone delay, then new zone opens |
| Rachio-style runoff | Cycle-and-soak (both fallback and HA paths) | Run/soak cycles |
| Burst pipe / popped head | High-flow alarm (HA) | `cancel_all`, notify |
| Closed manual valve, broken solenoid | No-flow alarm (HA) | `cancel_all`, notify |
| Leak with no zone running | Phantom-flow alarm (HA) | Notify |
| Supply failure during run | Low-pressure alarm (HA) | `cancel_all`, notify |
| Over-pressurized line | High-pressure alarm (HA) | Notify, optional `cancel_all` |
| Rain during a run | Rain wet automation + 12h hold | `cancel_all`, no resume |
| Cold boot without ever syncing NTP | `time_synced` gate (firmware) | Fallback does not fire until time is known |
| Power loss | Boot-safe relay state (firmware) | All relays OFF on boot, wait for NTP, resume fallback |
| Consolidated single point of failure | Accepted tradeoff (Q5) | On-device safety + HA-side alarms compensate |

## 10. Testing strategy

- **Bench testing (firmware).** Drive relays with LEDs or audible click before any 24VAC is connected. Verify each safety invariant explicitly: command two zones simultaneously, exceed the runtime cap, kill HA mid-run (disconnect API), boot with WiFi off, boot with WiFi on but no NTP, invoke `skip_next_run` and verify exactly one fire is consumed.
- **HA package testing.** Develop the package against a HA dev instance using simulated entities that mirror the device's. Trigger each automation by setting state manually; verify alarm thresholds and rain hold math.
- **Integration testing indoors.** Connect the real board to LEDs and one test 24VAC valve. Run a full simulated week of fallback fires plus several HA overrides, plus simulated rain trips and forced flow / pressure faults.
- **Field cutover.** Swap Rachio out on a weekend morning. Observe one full fallback run end-to-end, then a full HA-driven run with the takeover handshake. Verify per-zone water totals match expectations against a known reference (bucket test on one head).

## 11. Deliverables and repo layout

```
nedorachio/
├── README.md                 # BoM, wiring diagram, first-run guide
├── docs/
│   └── superpowers/
│       └── specs/
│           └── 2026-04-30-nedorachio-irrigation-controller-design.md
├── firmware/
│   ├── nedorachio.yaml       # ESPHome config
│   └── secrets.yaml.example  # Placeholders for WiFi / API key
└── homeassistant/
    └── packages/
        └── nedorachio.yaml   # Helpers + scripts + automations + dashboard
```

## 12. Roadmap (out of scope for this spec)

- ET-based watering minutes (HACS Smart Irrigation or equivalent), feeding `zone_N_duration_min`.
- Per-zone soil-type / slope inputs to compute cycle/soak ratios automatically.
- Mobile push grouping and quiet hours for alarms.
- OTA firmware artifact stored in Home Assistant for re-flash without dev tools.
- Persisting runtime state (current zone, accumulated minutes) across power loss if the deployment ever requires it.

## 13. Open issues to resolve during planning

- Exact GPIO assignment for relays, rain, flow, and pressure ADC. Determined on the bench; constraints documented in §6.3.
- Final flow-meter pulses-per-gallon calibration — published spec is approximate; one-time bench calibration against a known volume.
- Pressure transducer recalibration check after migration to confirm the existing linear calibration still holds with the new wiring.
