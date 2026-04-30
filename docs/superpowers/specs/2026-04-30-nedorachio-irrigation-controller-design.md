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
- Run a fully autonomous weekly schedule on the device (sequential zones with cycle-and-soak), with all logic — pre-flight gates, mid-run cancels, retries, rain hold, catch-up — in firmware. The system continues working when Home Assistant is unreachable.
- Let Home Assistant feed weather forecast data (`rain_mm_last_48h`) to the device, surface device state on a dashboard, route alarm notifications, and trigger manual / scheduled runs remotely. HA does not own irrigation logic.
- Integrate a rain sensor, a pulse flow meter, and the existing pressure transducer; expose all of them to Home Assistant; alarm, cancel, and retry on the failure modes described in §9.
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
                              │  ─ Weather feeder → rain_mm_last_48h   │
                              │  ─ Notification routes (alarm rising   │
                              │     edges from device)                 │
                              │  ─ Dashboard (Lovelace, vanilla cards) │
                              │  ─ Time source (ESPHome `homeassistant`│
                              │     time pulled by device)             │
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
                              │     per-zone runtime cap               │
                              │  ─ Cycle-and-soak engine               │
                              │  ─ Pre-flight gates (PSI, rain, fault) │
                              │  ─ Mid-run cancels + retry policy      │
                              │  ─ Weekly schedule + catch-up on boot  │
                              │  ─ Persisted stats & plan readouts     │
                              │  ─ Tunable parameters as HA entities   │
                              └──────────────┬─────────────────────────┘
                                             │
                          ┌──────────┬───────┼───────┬─────────────┐
                          │          │       │       │             │
                       Zone 1     Zone 4   Rain    Flow         Pressure
                       (24VAC)…   (24VAC)  switch  meter pulse  transducer (0–5V)
```

### 5.2 Responsibility split

The device owns *all* irrigation logic: scheduling, cycle-and-soak, safety interlocks, sensor-driven cancels, retries, rain hold, catch-up, and watering statistics. Home Assistant is reduced to:

- A **weather feeder** that pushes one number to the device — millimeters of rain in the last 48 hours — derived from a built-in HA weather entity.
- A **time source** via ESPHome's `time: homeassistant` integration (free, automatic), with a public-NTP `sntp` source as a secondary fallback.
- A **remote control surface** (manual zone run, trigger schedule, skip next, emergency stop) that calls device entities.
- A **dashboard** that reads device sensors (PSI, flow, plan, stats) and a **notification router** that fires off device alarm binary sensors.

The device must run the full irrigation cycle — including all alarms and retries — even when Home Assistant is unreachable. The only HA-derived value the device *needs* to make decisions is `rain_mm_last_48h`; if HA never pushes it (or pushes stale), the device falls back to the rain sensor and pressure-gate alone, no forecast skip.

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

**Zone control:**
- `switch.zone_1` … `switch.zone_8` — one per relay. Turning one ON goes through the safety layer; the relay is not driven directly by the switch state.
- `button.run_now_zone_N` — kick off a single-zone cycle-and-soak run for zone N using its configured `total_min` / `cycle_min` / `soak_min`. Same pre-flight gates apply.
- `button.run_full_cycle` — kick off the full sequential cycle (all enabled zones).
- `button.emergency_stop_all` — forces every zone off immediately and clears any pending retries.
- `button.skip_next_run` — vetoes only the next fallback fire (persisted in flash).
- `switch.fallback_schedule_enabled` — global enable for the on-device schedule.
- `switch.master_enable` — global "irrigation allowed" toggle. When OFF, no schedule fires and no manual run starts; running zones are aborted.
- `button.clear_fault` — clears latched per-zone fault state (after a retry-exhausted cancel).

**Sensors (live):**
- `binary_sensor.rain_sensor` — current wet/dry, polarity configurable.
- `sensor.flow_pulses_total` — monotonically increasing pulse count.
- `sensor.flow_rate_gpm` — instantaneous flow rate, smoothed.
- `sensor.pressure_psi` — calibrated PSI, linear from voltage divider.
- `binary_sensor.controller_online` — heartbeat / availability.
- `binary_sensor.time_synced` — true once a time source has synced at least once since boot.

**Plan readout (sensors):**
- `sensor.current_phase` — `idle` / `pre_flight` / `running` / `soaking` / `inter_zone_delay` / `fault`.
- `sensor.currently_running_zone` — zone number, or 0 if idle.
- `sensor.current_phase_remaining_s` — seconds left in the current phase.
- `sensor.run_progress_pct` — 0–100 across the full cycle.
- `sensor.next_planned_run` — timestamp of the next fallback fire.
- `sensor.last_run_started_at` / `sensor.last_run_finished_at` — timestamps.
- `sensor.last_run_outcome` — `completed` / `cancelled_rain` / `cancelled_flow` / `cancelled_pressure` / `cancelled_user` / `pre_flight_failed`.

**Stats (sensors, persisted in flash):**
- Per zone: `sensor.zone_N_run_count_total`, `sensor.zone_N_gallons_total`, `sensor.zone_N_seconds_total`, `sensor.zone_N_last_gallons`, `sensor.zone_N_last_duration_s`, `sensor.zone_N_last_run_at`.
- Today / this month: `sensor.gallons_today`, `sensor.gallons_this_month`, `sensor.runs_today`, `sensor.runs_this_month`. Daily/monthly counters reset on the first event after midnight / month rollover.

**Alarms (binary sensors — for HA notifications):**
- `binary_sensor.alarm_no_flow`, `binary_sensor.alarm_high_flow`, `binary_sensor.alarm_phantom_flow`, `binary_sensor.alarm_low_pressure`, `binary_sensor.alarm_high_pressure`, `binary_sensor.alarm_pre_flight_failed`, `binary_sensor.alarm_runtime_exceeded`. Each latches on detection and clears on `button.clear_fault` or on the next successful run start (where applicable).

**HA-driven inputs (number / button):**
- `number.rain_mm_last_48h` — pushed by HA from the weather entity. Default 0 if never written.
- All tunable parameters in §7.2.

### 7.2 Tunable parameters (exposed as `number` / `select` / `time` / `switch` entities)

Persisted in flash, editable from HA without re-flashing.

**Per-zone:**
- `total_min`, `cycle_min`, `soak_min`, `max_runtime_min` (hard cap).
- `min_flow_gpm`, `max_flow_gpm` — alarm bounds during run.

**Sensor calibration:**
- `pulses_per_gallon` (flow meter).
- Pressure linear-calibration points (voltage → PSI).

**Pressure gates (new):**
- `pressure_static_min_psi` — pre-flight floor (no schedule starts below this with no zone running).
- `pressure_static_max_psi` — pre-flight ceiling.
- `pressure_running_min_psi` — mid-run floor (low-pressure cancel).
- `pressure_high_psi` — high-pressure alarm threshold.
- `high_pressure_cancels_run` (switch) — if ON, high-pressure fires `cancel_all`; default OFF (notify-only).

**Flow gates:**
- `phantom_flow_gpm` — threshold for the phantom-flow alarm when no zone is on.
- `no_flow_grace_s` — how long after zone-on before the no-flow check arms (default 60s).
- `high_flow_grace_s` — how long the high-flow condition must hold (default 30s).

**Rain gates (new):**
- `rain_hold_hours_after_sensor` — minimum hold after the rain sensor goes dry (default 12h).
- `rain_mm_threshold_48h` — threshold above which `rain_mm_last_48h` blocks scheduled runs.
- `rain_hold_hours_after_forecast` — how long the forecast-based block lasts after the value drops below threshold (default 24h).
- `rain_mm_max_age_hours` — TTL on the HA-pushed value (default 12h). If HA hasn't refreshed `rain_mm_last_48h` within this window, the device treats it as 0 (fail-open). Prevents an offline HA from indefinitely blocking watering.

**Retry (new):**
- `retry_count` — max auto-retries after a flow/pressure cancel (default 1).
- `retry_delay_s` — seconds between cancel and retry (default 60).

**Catch-up (new):**
- `catchup_window_hours` — if a fallback fire was missed within the last N hours, run it on next boot/NTP sync (default 6, 0 disables).

**Sequencing & safety:**
- `inter_zone_delay_s` (default 2).

**Schedule:**
- Days-of-week (7 booleans, default Tue + Sat).
- Start time (default 06:00 local).

### 7.3 Hard safety invariants (always enforced)

1. **At most one zone ON at a time.** A request to turn zone N on while zone M is on first turns M off and waits the inter-zone delay.
2. **Inter-zone close→open delay.** Configurable; ensures pressure settles before the next valve opens.
3. **Per-zone hard runtime cap.** On exceedance, zone is shut off, `alarm_runtime_exceeded` latches, the run is marked `cancelled_user`-equivalent (no retry).
4. **Boot-safe state.** On boot, every relay is forced OFF before WiFi connects.
5. **`master_enable` OFF or `emergency_stop_all` pressed** aborts everything immediately.

These apply to every code path that touches a relay — fallback schedule, `run_now_zone_N`, `run_full_cycle`, manual `switch.zone_N`.

### 7.4 Pre-flight gates (checked before any run starts)

A "run" here means either a scheduled fallback fire, `run_full_cycle`, or `run_now_zone_N`. Each gate, if it fails, sets `last_run_outcome = pre_flight_failed`, latches `alarm_pre_flight_failed`, and aborts. Gates evaluated in order:

1. `master_enable` is ON.
2. `time_synced` is true.
3. Rain sensor is dry, AND the rain-sensor hold has expired (`now − last_wet_at >= rain_hold_hours_after_sensor`).
4. Forecast hold: `rain_mm_last_48h <= rain_mm_threshold_48h` (treating values older than `rain_mm_max_age_hours` as 0), AND if it was previously above threshold, the forecast hold has expired.
5. Static-pressure gate: `pressure_static_min_psi <= pressure_psi <= pressure_static_max_psi` with no zone running. Brief settle delay before reading.
6. No alarm is currently latched (or, if `clear_fault` was just pressed, latches are clear).

For a fallback fire only, additionally:

7. `fallback_schedule_enabled` is ON.
8. `skip_next_run` was not pressed since the last fire (otherwise it consumes the skip and aborts cleanly without latching `pre_flight_failed`).

### 7.5 Cycle-and-soak engine

- Iterates enabled zones in order. For each: alternate "running" (relay ON for `cycle_min`) and "soaking" (relay OFF for `soak_min`) phases until the accumulated running time reaches `total_min`. Inserts `inter_zone_delay_s` between zones.
- During every running phase: tick the flow / pressure cancel checks (§7.6). During soaking and inter-zone phases: skip flow checks (no zone is on); pressure is observed but doesn't cancel (no run in progress for that zone).
- `current_phase`, `currently_running_zone`, `current_phase_remaining_s`, `run_progress_pct` are updated continuously.
- On any cancel: stop the relay, evaluate retry policy (§7.7).
- Successful zone completion increments stats counters and updates `zone_N_*` sensors.

### 7.6 Mid-run cancel rules

Evaluated on a 1-second tick during running phases:

- **No-flow:** zone running for ≥ `no_flow_grace_s`, `flow_rate_gpm < zone_N_min_flow_gpm` → cancel with cause `flow`.
- **High-flow:** `flow_rate_gpm > zone_N_max_flow_gpm` continuously for ≥ `high_flow_grace_s` → cancel with cause `flow`.
- **Low-pressure:** zone running for ≥ 30s, `pressure_psi < pressure_running_min_psi` → cancel with cause `pressure`.
- **High-pressure:** `pressure_psi > pressure_high_psi` for ≥ 10s → latch `alarm_high_pressure`; if `high_pressure_cancels_run` is ON, cancel with cause `pressure`.
- **Phantom flow** (always armed when no zone is on, including soaking phases): `flow_rate_gpm > phantom_flow_gpm` for ≥ 5 minutes → latch `alarm_phantom_flow`. Notify-only (nothing to cancel).
- **Rain wet:** rain sensor goes wet mid-run → cancel with cause `rain` (no retry, no resume — see Q7).

### 7.7 Retry and fault latching

When a run is cancelled with cause `flow` or `pressure`:

1. Turn the zone off, hold for `retry_delay_s`.
2. If `retries_used_this_run < retry_count`: re-arm pre-flight gates (§7.4); if they pass, restart the *current* zone from the beginning of its cycle-and-soak (not the whole cycle). Increment `retries_used_this_run`.
3. If retries are exhausted: latch the corresponding alarm, set `last_run_outcome` accordingly, abort the remainder of the cycle (do not move on to subsequent zones — operator should investigate), set `current_phase = fault`. Cleared by `clear_fault` or by the next manual run that passes pre-flight.

Cancels caused by `rain` or by `master_enable` / `emergency_stop_all` do not retry.

### 7.8 Catch-up on boot

After a boot completes and `time_synced` becomes true:

- Look up the most recent scheduled fire time within the last `catchup_window_hours`.
- If there is one and `last_run_started_at` is older than that fire time (the fire was missed, not already executed), evaluate pre-flight gates and, if they pass, start the cycle now.
- If `catchup_window_hours` is 0, this is disabled.
- Only the *most recent* missed fire is caught up — never multiple back-to-back runs.

### 7.9 Time sources

- Primary: ESPHome `time: homeassistant` (HA pushes time when connected).
- Fallback: ESPHome `time: sntp` against a public NTP pool when the HA push is stale or unavailable.
- `time_synced` is true once either source has produced a sync since boot.
- Free-running clock holds across transient WiFi/HA loss.
- Power loss is out of scope: device boots, waits for sync, then arms (and may run catch-up if §7.8 applies).

## 8. Home Assistant configuration

HA owns no irrigation logic. The package is just a thin layer that feeds weather data to the device, surfaces device state on a dashboard, and routes alarm notifications. Ships as a single HA Package YAML in `homeassistant/packages/nedorachio.yaml`.

### 8.1 Weather feeder (the one automation that matters)

A single automation, fired every 10 minutes, computes "millimeters of rain in the last 48 hours" from the configured weather entity (uses HA's `weather.get_forecasts` service plus the recorder for past observed precipitation, or a more direct sensor if one is exposed) and writes it to the device's `number.rain_mm_last_48h`. If the weather entity is unavailable, the automation skips (the device retains the last value or zero); operationally this means a stale forecast can't accidentally block irrigation forever — operator can also override `number.rain_mm_last_48h` directly from the dashboard.

### 8.2 Time push

ESPHome's `time: homeassistant` source pulls time from HA automatically once connected — no automation required. The package documents nothing here; it is set up entirely on the firmware side (§7.9).

### 8.3 Notification routes

One automation per device alarm binary sensor (no_flow, high_flow, phantom_flow, low_pressure, high_pressure, pre_flight_failed, runtime_exceeded). Each fires a notification (mobile push or whatever the user configures) on the alarm's rising edge with a short message including the affected zone (when applicable) and the latest sensor values. No cancel actions — the device has already cancelled.

### 8.4 Dashboard

Single Lovelace view, vanilla cards only:

- **Top row:** master enable toggle, fallback schedule enable toggle, emergency stop button, "skip next run" button, "clear fault" button.
- **Status:** `current_phase`, `currently_running_zone`, `current_phase_remaining_s`, `run_progress_pct`, `next_planned_run`, `last_run_outcome`.
- **Per-zone tiles (×4):** name, enabled toggle, total/cycle/soak number entries, "run now" button, last-run gallons + duration, lifetime gallons + run count, latest min/max flow setpoints.
- **Live sensors:** rain wet/dry, flow GPM, pressure PSI, today's gallons, this month's gallons.
- **Alarms feed:** state of every `alarm_*` binary sensor with timestamp.
- **Tuning panel (collapsed by default):** all calibration & threshold `number` entities from §7.2 grouped logically.

### 8.5 What HA does *not* do

For clarity (and to bound implementation scope), HA does not run cycle-and-soak, does not orchestrate retries, does not enforce rain hold, does not gate pre-flight, does not maintain stats. Every one of those is on-device. HA only feeds weather and surfaces state.

## 9. Failure modes and mitigations

| Failure | Detection | Mitigation |
|---|---|---|
| Stuck zone (any cause) | Per-zone runtime cap (firmware §7.3) | Zone shut off, `alarm_runtime_exceeded` latches |
| HA / WiFi outage mid-run | Device runs autonomously (firmware §5.2) | Run continues uninterrupted; sensor cancels still active |
| Two zones commanded on simultaneously | Single-zone invariant (firmware §7.3) | Earlier zone closed, inter-zone delay, then new zone opens |
| Rachio-style runoff | Cycle-and-soak (firmware §7.5) | Run/soak cycles per zone |
| Supply failure before a run | Pre-flight static-pressure gate (firmware §7.4) | Run aborts before any zone opens, `alarm_pre_flight_failed` latches |
| Burst pipe / popped head | High-flow cancel (firmware §7.6) | Zone cancelled, retry once, then fault latch |
| Closed manual valve, broken solenoid | No-flow cancel (firmware §7.6) | Zone cancelled, retry once, then fault latch |
| Leak with no zone running | Phantom-flow alarm (firmware §7.6) | `alarm_phantom_flow` latches; HA notifies; nothing to cancel |
| Supply failure during run | Low-pressure cancel (firmware §7.6) | Zone cancelled, retry once, then fault latch |
| Over-pressurized line | High-pressure alarm (firmware §7.6) | Latches alarm; cancels run if `high_pressure_cancels_run` is ON |
| Transient pressure dip | Retry policy (firmware §7.7) | One auto-retry after `retry_delay_s` |
| Rain during a run | Rain-sensor cancel (firmware §7.6) | Zone aborted; no retry, no resume; rain hold engages |
| Heavy recent rainfall (forecast) | `rain_mm_last_48h` pre-flight (firmware §7.4) | Schedule fire blocked; forecast hold engages |
| Cold boot without ever syncing time | `time_synced` gate (firmware §7.4 / §7.9) | Fallback does not fire until time is known |
| Missed fire across reboot | Catch-up logic (firmware §7.8) | Most recent missed fire within window runs once |
| Power loss | Boot-safe relay state (firmware §7.3) | All relays OFF on boot, wait for sync, resume |
| Consolidated single point of failure | Accepted tradeoff (Q5) | On-device safety + alarm latches + retries compensate |

## 10. Testing strategy

- **Bench testing (firmware).** Drive relays with LEDs or audible click before any 24VAC is connected. Verify each safety invariant and pre-flight gate explicitly: command two zones simultaneously; exceed the runtime cap; force `master_enable` OFF mid-run; toggle the rain sensor mid-run; force `pressure_psi` outside static bounds at pre-flight; force `flow_rate_gpm` outside running bounds and verify exactly one retry then fault latch; force `flow_rate_gpm > 0` with no zone on and verify the phantom alarm; press `skip_next_run` and verify exactly one fire is consumed; reboot during a run and verify catch-up runs at most once.
- **HA package testing.** Run the package on a HA dev instance pointed at the device. Verify the weather-feeder writes `number.rain_mm_last_48h` and that pushing a value above threshold blocks pre-flight. Verify alarm-binary-sensor → notification routes fire on rising edge.
- **Integration testing indoors.** Connect the real board to LEDs and one test 24VAC valve. Run a simulated full week of fallback fires plus several manual fires, plus simulated rain trips and forced flow / pressure faults end-to-end.
- **Field cutover.** Swap Rachio out on a weekend morning. Observe one full fallback run end-to-end, then a `run_full_cycle` triggered manually. Verify per-zone gallon totals against a known reference (bucket test on one head) to lock in `pulses_per_gallon` and the per-zone flow alarm bounds.

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
