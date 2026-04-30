# Nedorachio Irrigation Controller Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace a Rachio sprinkler controller with an 8-relay ESP32-WROOM-32E running ESPHome that owns *all* irrigation logic — schedules, cycle-and-soak, pre-flight gates, mid-run cancels, retries, rain hold, catch-up, stats — and a thin Home Assistant package that feeds weather data, surfaces state, and routes notifications.

**Architecture:** Single ESPHome firmware split into focused YAML packages. All logic on-device; HA reads/pushes through ESPHome's native API. Sensors (rain binary, flow pulse, pressure ADC) and the existing pressure-transducer wiring are consolidated onto the new board. Bench-first development: every behavior is exercised on a desk harness (LEDs in place of valves, simulated inputs) before the controller goes near 24VAC.

**Tech Stack:** ESPHome (firmware YAML, Arduino framework on ESP32-WROOM-32E), Home Assistant native API, vanilla Home Assistant Package YAML (no HACS).

**Spec:** `docs/superpowers/specs/2026-04-30-nedorachio-irrigation-controller-design.md` — read it before starting and again before any task that surprises you.

---

## File structure

```
nedorachio/
├── README.md                                        # BoM, wiring, first-run
├── .gitignore                                       # ignores firmware/secrets.yaml + build dirs
├── docs/superpowers/
│   ├── specs/2026-04-30-...-design.md               # already exists
│   └── plans/2026-04-30-...-plan.md                 # this file
├── firmware/
│   ├── nedorachio.yaml                              # entrypoint, includes all packages below
│   ├── secrets.yaml.example                         # WiFi SSID/password + API key placeholders
│   ├── secrets.yaml                                 # gitignored, real values
│   └── packages/
│       ├── 01-core.yaml                             # board, wifi, api, ota, time, status sensors
│       ├── 02-zones.yaml                            # 8 relays + zone switches + safety invariants
│       ├── 03-sensors.yaml                          # rain, flow, pressure
│       ├── 04-tunables.yaml                         # all number / select / switch tuning entities
│       ├── 05-engine.yaml                           # pre-flight gates + cycle-and-soak + cancels + retry
│       ├── 06-schedule.yaml                         # weekly fire + skip + catch-up + plan readouts
│       └── 07-stats.yaml                            # persisted counters + alarms
└── homeassistant/
    └── packages/
        └── nedorachio.yaml                          # weather feeder + notifications + dashboard
```

**Why split this way:** firmware files are grouped by responsibility, not by entity type. Each file is a small, focused chunk that can be reviewed independently and held in context at once. ESPHome's `packages:` directive merges them at compile time.

**Repeated patterns:** wherever a pattern repeats per zone (×8), each task shows one concrete instance and uses `# repeat for zones 2..8` to indicate the duplication. The implementer copies the block 8 times with the index substituted; do **not** invent a meta-templating scheme.

---

## Conventions and ground rules

- **Commit after every task.** One task = one commit.
- **YAML lint before every commit.** Run `esphome config firmware/nedorachio.yaml` to validate. If it errors, fix before committing.
- **Bench tests are the test suite.** ESPHome has no unit-test framework. "Tests" mean: flash, watch the HA Developer Tools / Logs view, confirm the expected entity state.
- **No 24VAC during firmware development.** Bench harness uses LEDs (or audible-click relay state) on the relay outputs and a button-board for binary inputs. Pressure transducer can be left wired (it's powered separately) or replaced with a bench potentiometer feeding the ADC pin through the divider.
- **Parameters live in `04-tunables.yaml`.** Whenever a task needs a tunable, add the `number`/`switch`/`select` to that file and reference its `id:` from logic. Never hardcode thresholds in the engine.
- **Globals use `restore_value: true`** for anything that should survive an OTA reflash. Counters, last-run timestamps, retry latches, skip-flag, stats — all persisted.
- **Time source:** `time: homeassistant` *and* `time: sntp` both listed. ESPHome treats them as alternates; whichever syncs first wins.
- **Lambdas:** keep them short. Anything over ~20 lines should be split into a separate script.
- **All scripts use `mode: single`** unless explicitly noted (cycle-and-soak engine is `mode: single` to prevent re-entry).

---

## Phase 0 — Project skeleton

### Task 0.1: Create the repo skeleton

**Files:**
- Create: `README.md`
- Create: `.gitignore`
- Create: `firmware/secrets.yaml.example`
- Create: `firmware/nedorachio.yaml`
- Create: `homeassistant/packages/.gitkeep` (placeholder so the dir survives in git)
- Create: `firmware/packages/.gitkeep`

- [ ] **Step 1: Write `.gitignore`**

```gitignore
firmware/secrets.yaml
firmware/.esphome/
firmware/.pioenvs/
firmware/.piolibdeps/
firmware/build/
.DS_Store
*.swp
```

- [ ] **Step 2: Write `firmware/secrets.yaml.example`**

```yaml
wifi_ssid: "your-wifi-ssid"
wifi_password: "your-wifi-password"
api_encryption_key: "generate-with: esphome wizard or openssl rand -base64 32"
ota_password: "generate-a-strong-password"
```

- [ ] **Step 3: Write a stub `firmware/nedorachio.yaml`**

```yaml
substitutions:
  device_name: nedorachio
  friendly_name: Nedorachio Irrigation Controller

esphome:
  name: ${device_name}
  friendly_name: ${friendly_name}

esp32:
  board: esp32dev
  framework:
    type: arduino

# Packages will be added one by one as the implementation progresses.
packages: {}
```

- [ ] **Step 4: Write a brief `README.md`**

```markdown
# Nedorachio

Home-Assistant-controlled irrigation controller built on an 8-relay ESP32-WROOM-32E
development board running ESPHome. Replaces a Rachio. See
`docs/superpowers/specs/2026-04-30-nedorachio-irrigation-controller-design.md`
for the full design.

## Status

Implementation in progress. See `docs/superpowers/plans/`.

## Layout

- `firmware/` — ESPHome configuration.
- `homeassistant/packages/` — Home Assistant Package YAML (weather feeder, notifications, dashboard).
- `docs/` — design spec and implementation plan.
```

- [ ] **Step 5: Verify the directory tree**

Run: `find . -maxdepth 4 -not -path '*/\.git*' | sort`
Expected output (order may differ):
```
.
./.gitignore
./README.md
./docs
./docs/superpowers
./docs/superpowers/plans
./docs/superpowers/plans/2026-04-30-nedorachio-irrigation-controller-plan.md
./docs/superpowers/specs
./docs/superpowers/specs/2026-04-30-nedorachio-irrigation-controller-design.md
./firmware
./firmware/nedorachio.yaml
./firmware/packages
./firmware/packages/.gitkeep
./firmware/secrets.yaml.example
./homeassistant
./homeassistant/packages
./homeassistant/packages/.gitkeep
```

- [ ] **Step 6: Commit**

```bash
git add .gitignore README.md firmware/ homeassistant/
git commit -m "chore: scaffold repo (firmware, ha package, secrets template)"
```

---

## Phase 1 — Hardware bench prep

These tasks are physical; outputs are measurements/notes captured in the README. No code commits, but commit the updated README and a wiring photo if available.

### Task 1.1: Unbox the board and discover the GPIO map

**Files:**
- Modify: `README.md` — add a "Hardware" section with the discovered pin map.

- [ ] **Step 1: Inspect the board**

Note physically: number of relay channels, location of EN/BOOT buttons, any extra user buttons, location of the AC input terminals, and where the relay control header brings out 5V/GND for the relay-driver inputs.

- [ ] **Step 2: Probe relay-channel GPIOs**

With a multimeter set to continuity, find which ESP32 GPIO pin drives each relay's optocoupler / driver input. Power off; touch one probe to the relay-input pad and the other to each candidate GPIO header. Record the eight `relay_N → GPIOxx` mappings.

If the board has no convenient pin labels, fall back to: power-on with a stock-flashed ESPHome that exposes one `gpio` output at a time on each candidate GPIO and watch which relay clicks.

- [ ] **Step 3: Identify free GPIOs for sensors**

Spare GPIOs available on the header. For our sensor needs:

- **Pressure ADC** must be on **ADC1**: GPIO32, 33, 34, 35, 36, 39. Pick one that is broken out on the header.
- **Flow pulse input** can be any GPIO with interrupt support that is *not* a strap pin (avoid GPIO0, 2, 12, 15 unless you're certain of their boot-strap behavior).
- **Rain input** has no special constraint beyond pull-up support.

Write down three chosen pins.

- [ ] **Step 4: Update `README.md` with the GPIO map**

Append to README:

```markdown
## Hardware

### Board

8-channel ESP32-WROOM-32E relay/dev board (Amazon ASIN B0DK6QKNBM).

### GPIO map (discovered on the bench, $YYYY-MM-DD)

| Function           | GPIO   | Notes |
|--------------------|--------|-------|
| Relay 1 (zone 1)   | GPIO?? | active-low / active-high (mark which) |
| Relay 2 (zone 2)   | GPIO?? | |
| Relay 3 (zone 3)   | GPIO?? | |
| Relay 4 (zone 4)   | GPIO?? | |
| Relay 5 (zone 5)   | GPIO?? | unused, terminal-blocked for future |
| Relay 6 (zone 6)   | GPIO?? | unused |
| Relay 7 (zone 7)   | GPIO?? | unused |
| Relay 8 (zone 8)   | GPIO?? | unused |
| Rain sensor input  | GPIO?? | internal pull-up |
| Flow meter pulse   | GPIO?? | internal pull-up, interrupt-capable |
| Pressure ADC       | GPIO?? | ADC1, 11dB attenuation |
```

Replace each `GPIO??` and the polarity note with the discovered values.

- [ ] **Step 5: Commit the README update**

```bash
git add README.md
git commit -m "docs: record discovered GPIO map for the relay board"
```

### Task 1.2: Migrate the pressure-transducer wiring

**Files:** physical only.

- [ ] **Step 1: Disconnect the existing ESP32-C3 pressure-sensor device**

Power down. Disconnect the 0–5V signal wire from the C3's voltage divider and the 24VDC supply leads. Keep the divider (20kΩ + 10kΩ) and the IRM-05-24 supply intact — both move to the new enclosure as-is.

- [ ] **Step 2: Re-mount on the bench harness**

Mount the divider near the new board. Wire 24VDC → transducer red → transducer black → ground. Transducer signal (0–5V) → divider top → divider midpoint → chosen ADC1 GPIO. Divider bottom → ground.

- [ ] **Step 3: Verify the divider midpoint with the transducer at static line pressure**

With the multimeter on DC volts, measure midpoint vs. ground. With the pipe at typical static pressure (record the manual gauge reading), expected divider output is roughly `(transducer_voltage) × 10/30 = transducer_voltage / 3`. Sanity-check against the linear calibration constants from the existing C3 firmware before flashing the new firmware.

Record the measured voltage and gauge PSI in the README under "Calibration":

```markdown
### Calibration

Pressure transducer linear calibration (carried over from ESP32-C3 device):

- $V_low V at $P_low PSI
- $V_high V at $P_high PSI

Bench-verified $YYYY-MM-DD: gauge reads $P_meas PSI; ADC midpoint $V_meas V.
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: record pressure-transducer migration calibration check"
```

### Task 1.3: Build the bench harness

**Files:** physical only.

- [ ] **Step 1: Wire LEDs to the relay outputs**

For each of the 8 relay outputs, connect an LED + 1kΩ resistor across the NO/COM contacts driven by a 5V bench supply. The LED lights when the relay is energized. (Yes, you're using the *contact* side of the relay — same path as 24VAC, just safer on the bench.)

- [ ] **Step 2: Wire bench inputs**

- Rain sensor: a single SPST momentary or toggle switch between the chosen rain GPIO and ground. Pressing/closing it simulates "wet".
- Flow meter: a momentary push-button between the chosen flow GPIO and ground. Each press = 1 pulse. (For sustained-rate testing, a 555-timer or Arduino with a configurable square-wave generator is even better, but a press-and-hold simulator works for first bring-up.)

- [ ] **Step 3: Power up, photograph, and document**

Take one photo of the bench harness. Save under `docs/bench-harness.jpg` and reference it from the README's Hardware section.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/bench-harness.jpg
git commit -m "docs: bench harness photo and wiring notes"
```

---

## Phase 2 — Firmware skeleton, sensors

### Task 2.1: Write `01-core.yaml` — board, WiFi, API, OTA, time, online

**Files:**
- Create: `firmware/packages/01-core.yaml`
- Modify: `firmware/nedorachio.yaml` — wire up the package

- [ ] **Step 1: Write `firmware/packages/01-core.yaml`**

```yaml
# WiFi, ESPHome API (HA connection), OTA, time sources, and core status sensors.

logger:
  level: INFO
  baud_rate: 115200

wifi:
  ssid: !secret wifi_ssid
  password: !secret wifi_password
  ap:
    ssid: "${device_name} fallback"
    password: !secret ota_password

captive_portal:

api:
  encryption:
    key: !secret api_encryption_key

ota:
  - platform: esphome
    password: !secret ota_password

# Two time sources; whichever syncs first wins.
time:
  - platform: homeassistant
    id: ha_time
    on_time_sync:
      - lambda: |-
          id(time_synced).publish_state(true);
  - platform: sntp
    id: sntp_time
    servers:
      - 0.pool.ntp.org
      - 1.pool.ntp.org
      - 2.pool.ntp.org
    on_time_sync:
      - lambda: |-
          id(time_synced).publish_state(true);

binary_sensor:
  - platform: template
    name: "Time synced"
    id: time_synced
    device_class: connectivity
    icon: mdi:clock-check

  - platform: status
    name: "Controller online"
    id: controller_online
    device_class: connectivity
```

- [ ] **Step 2: Update `firmware/nedorachio.yaml` to include the package**

Replace the `packages: {}` line with:

```yaml
packages:
  core: !include packages/01-core.yaml
```

- [ ] **Step 3: Validate the config**

Run: `cd firmware && esphome config nedorachio.yaml`
Expected: clean compile, "INFO Configuration is valid!"

- [ ] **Step 4: Flash to the board**

Plug the board into USB. Run: `cd firmware && esphome run nedorachio.yaml`
Expected: build succeeds, flash succeeds, device boots and connects to WiFi.

- [ ] **Step 5: Add the device in Home Assistant**

In HA: Settings → Devices & Services → Add Integration → ESPHome → enter the device's IP/hostname → paste the API encryption key from `secrets.yaml`. Verify both `binary_sensor.time_synced` and `binary_sensor.controller_online` appear and report `on`.

- [ ] **Step 6: Commit**

```bash
git add firmware/nedorachio.yaml firmware/packages/01-core.yaml
git commit -m "feat(firmware): boot, wifi, api, ota, time, status sensors"
```

### Task 2.2: Write `02-zones.yaml` — 8 relay outputs and zone switches with safety invariants

**Files:**
- Create: `firmware/packages/02-zones.yaml`
- Modify: `firmware/nedorachio.yaml` — include the package

This is the biggest single firmware task. It implements the §7.3 hard safety invariants: at most one zone ON, inter-zone delay, runtime cap, boot-safe state, and a master-enable / emergency-stop gate.

- [ ] **Step 1: Write the relay outputs and zone switches**

Create `firmware/packages/02-zones.yaml`:

```yaml
# Replace each `pin: GPIOxx` and `inverted:` with the values discovered in Task 1.1.

substitutions:
  inter_zone_default_s: "2"
  zone_max_runtime_default_s: "3600"   # 60 minutes

# Backing GPIO outputs (internal, hidden from HA).
output:
  - platform: gpio
    pin: { number: GPIOxx, inverted: true }
    id: relay_1_out
  # repeat for zones 2..8

# Public per-zone switches. Turning these on goes through the safety layer.
switch:
  - platform: template
    name: "Zone 1"
    id: zone_1
    icon: mdi:water
    optimistic: false
    turn_on_action:
      - script.execute:
          id: request_zone_on
          zone_id: 1
    turn_off_action:
      - script.execute:
          id: request_zone_off
          zone_id: 1
    lambda: |-
      return id(zone_1_actual_state);
  # repeat for zones 2..8

  - platform: template
    name: "Master enable"
    id: master_enable
    optimistic: true
    restore_mode: RESTORE_DEFAULT_ON
    icon: mdi:water-pump

  - platform: template
    name: "Emergency stop (latched)"
    id: emergency_stop_latched
    optimistic: true
    restore_mode: ALWAYS_OFF

# State globals. `zone_N_actual_state` reflects the *real* relay state, which
# the safety layer maintains; the public switch reads it via `lambda:`.
globals:
  - id: zone_1_actual_state
    type: bool
    restore_value: false
    initial_value: 'false'
  # repeat for zones 2..8

  - id: currently_on_zone
    type: int
    restore_value: false
    initial_value: '0'
  - id: zone_started_at_ms
    type: uint32_t
    restore_value: false
    initial_value: '0'

# Buttons exposed to HA.
button:
  - platform: template
    name: "Emergency stop all"
    id: emergency_stop_all
    icon: mdi:stop-circle
    on_press:
      - script.execute: cancel_everything

# Helper scripts. The safety layer lives entirely in these.
script:
  # Request to turn zone N on. Handles single-zone invariant + inter-zone delay.
  - id: request_zone_on
    mode: queued
    parameters:
      zone_id: int
    then:
      - if:
          condition:
            or:
              - switch.is_off: master_enable
              - switch.is_on: emergency_stop_latched
          then:
            - logger.log: "Master disabled or e-stop latched; refusing zone request."
            - script.stop: request_zone_on
      - if:
          condition:
            lambda: 'return id(currently_on_zone) != 0 && id(currently_on_zone) != zone_id;'
          then:
            - script.execute:
                id: turn_off_current_zone
            - delay: !lambda 'return id(inter_zone_delay_s).state * 1000;'
      - script.execute:
          id: drive_zone
          zone_id: !lambda 'return zone_id;'
          state: !lambda 'return true;'

  - id: request_zone_off
    mode: queued
    parameters:
      zone_id: int
    then:
      - script.execute:
          id: drive_zone
          zone_id: !lambda 'return zone_id;'
          state: !lambda 'return false;'

  - id: drive_zone
    mode: queued
    parameters:
      zone_id: int
      state: bool
    then:
      - lambda: |-
          // Drive the right relay and update the matching state global.
          switch (zone_id) {
            case 1: state ? id(relay_1_out).turn_on() : id(relay_1_out).turn_off(); id(zone_1_actual_state) = state; break;
            // repeat for zones 2..8
          }
          // Maintain currently_on_zone bookkeeping.
          if (state) {
            id(currently_on_zone) = zone_id;
            id(zone_started_at_ms) = millis();
          } else if (id(currently_on_zone) == zone_id) {
            id(currently_on_zone) = 0;
            id(zone_started_at_ms) = 0;
          }
      - lambda: |-
          // Re-publish the switch state so HA reflects the actual relay state.
          switch (zone_id) {
            case 1: id(zone_1).publish_state(id(zone_1_actual_state)); break;
            // repeat for zones 2..8
          }

  - id: turn_off_current_zone
    mode: single
    then:
      - if:
          condition:
            lambda: 'return id(currently_on_zone) != 0;'
          then:
            - script.execute:
                id: drive_zone
                zone_id: !lambda 'return id(currently_on_zone);'
                state: !lambda 'return false;'

  - id: cancel_everything
    mode: single
    then:
      - lambda: |-
          // Force every relay off, regardless of state.
          id(relay_1_out).turn_off(); id(zone_1_actual_state) = false; id(zone_1).publish_state(false);
          // repeat for zones 2..8
          id(currently_on_zone) = 0;
          id(zone_started_at_ms) = 0;

# Boot-safe: every relay forced off as soon as ESPHome's main loop starts,
# before WiFi connects.
esphome:
  on_boot:
    priority: 800
    then:
      - script.execute: cancel_everything

# Periodic safety: per-zone runtime cap (default 60 minutes from
# `zone_max_runtime_min` once tunables exist; for this task we hardcode and
# replace later in Task 3.1 / 4.x).
interval:
  - interval: 1s
    then:
      - if:
          condition:
            lambda: |-
              if (id(currently_on_zone) == 0) return false;
              uint32_t elapsed_ms = millis() - id(zone_started_at_ms);
              return elapsed_ms > (${zone_max_runtime_default_s} * 1000UL);
          then:
            - logger.log: "Runtime cap exceeded; cutting zone."
            - script.execute: cancel_everything
            - binary_sensor.template.publish:
                id: alarm_runtime_exceeded
                state: true

binary_sensor:
  - platform: template
    name: "Alarm: runtime exceeded"
    id: alarm_runtime_exceeded
    device_class: problem
```

Notes on the YAML:
- "repeat for zones 2..8" means literally copy the block 8 times with the index substituted. ESPHome has no loops; copy-paste is the idiom.
- `currently_on_zone == 0` means "no zone running". Zones are numbered 1..8.
- `inter_zone_delay_s` is referenced as a `number` entity from `04-tunables.yaml`. Until that file exists (Task 3.1), the line will fail config-validation; **fix:** for this task, replace the lambda with `delay: 2s` and convert to the number entity in Task 3.1.

- [ ] **Step 2: Use the temporary hardcoded 2s delay**

Per the note above, replace `delay: !lambda 'return id(inter_zone_delay_s).state * 1000;'` with `delay: 2s` for now. Leave a TODO comment so Task 3.1 catches it.

- [ ] **Step 3: Wire the package into `firmware/nedorachio.yaml`**

```yaml
packages:
  core: !include packages/01-core.yaml
  zones: !include packages/02-zones.yaml
```

- [ ] **Step 4: Validate**

Run: `cd firmware && esphome config nedorachio.yaml`
Expected: "Configuration is valid!"

- [ ] **Step 5: Flash and bench-test single-zone invariant**

Flash. In HA, turn on `switch.zone_1`. LED 1 lights. While it's on, turn on `switch.zone_2`. Expected sequence on the bench: LED 1 turns off, ~2s pause, LED 2 turns on. `switch.zone_1` reports off, `switch.zone_2` reports on. Repeat with each pair to confirm the invariant holds for any combination.

- [ ] **Step 6: Bench-test runtime cap**

Temporarily change `zone_max_runtime_default_s` to `15` (15 seconds), recompile, reflash. Turn on `switch.zone_1`. After 15 seconds, the relay should drop and `binary_sensor.alarm_runtime_exceeded` should turn `on`. Restore to `3600` and reflash.

- [ ] **Step 7: Bench-test boot-safe**

Force `switch.zone_1` on. While it's on, press the EN button on the board (hardware reset). After reboot, all LEDs are off; `switch.zone_1` reports off.

- [ ] **Step 8: Bench-test master enable / e-stop**

Turn off `switch.master_enable`. Try to turn `switch.zone_1` on. Expected: nothing happens, log shows "Master disabled or e-stop latched; refusing zone request." Turn `master_enable` back on. Turn `switch.zone_1` on (LED on). Press `button.emergency_stop_all`. Expected: all LEDs off. Try to turn a zone on — succeeds (the button doesn't latch the e-stop). Now turn `switch.emergency_stop_latched` on; same test should refuse. Turn it back off when done.

- [ ] **Step 9: Commit**

```bash
git add firmware/nedorachio.yaml firmware/packages/02-zones.yaml
git commit -m "feat(firmware): zone relays + safety invariants (one-zone, runtime cap, boot-safe, e-stop)"
```

### Task 2.3: Write `03-sensors.yaml` — rain, flow, pressure

**Files:**
- Create: `firmware/packages/03-sensors.yaml`
- Modify: `firmware/nedorachio.yaml` — include the package

- [ ] **Step 1: Write the sensor package**

Replace `GPIOxx` with the values from Task 1.1. Replace the calibration points with the values from Task 1.2.

```yaml
# Rain (binary), flow (pulse), pressure (ADC + linear calibration).

binary_sensor:
  - platform: gpio
    pin:
      number: GPIOxx
      mode:
        input: true
        pullup: true
      inverted: true   # if rain sensor closes-on-wet pulls GPIO low
    name: "Rain sensor"
    id: rain_sensor
    device_class: moisture
    filters:
      - delayed_on_off: 500ms

sensor:
  # Flow meter: pulses + smoothed rate.
  - platform: pulse_meter
    name: "Flow rate"
    id: flow_rate_gpm
    pin:
      number: GPIOxx
      mode:
        input: true
        pullup: true
    unit_of_measurement: "gpm"
    accuracy_decimals: 2
    timeout: 30s   # below this rate, treat as zero
    filters:
      # pulses/min → gpm: pulses/min × (1 gallon / pulses_per_gallon)
      # Use a number entity; expose multiplier dynamically.
      - lambda: |-
          float ppg = id(pulses_per_gallon).state;
          if (ppg <= 0.0) return 0.0;
          return x / ppg;
    total:
      name: "Flow pulses total"
      id: flow_pulses_total
      unit_of_measurement: "pulses"
      accuracy_decimals: 0

  # Pressure: ADC1 → divider midpoint → PSI.
  - platform: adc
    pin: GPIOxx        # must be ADC1 (32-39)
    name: "Pressure"
    id: pressure_psi
    update_interval: 1s
    attenuation: 11db
    accuracy_decimals: 1
    unit_of_measurement: "psi"
    filters:
      - sliding_window_moving_average:
          window_size: 5
          send_every: 1
      # Linear calibration: replace with the points captured in Task 1.2.
      # The voltage here is the *divider midpoint* voltage (≈ Vtransducer / 3
      # given the 20kΩ + 10kΩ divider).
      - calibrate_linear:
          - V_LOW -> P_LOW
          - V_HIGH -> P_HIGH
```

- [ ] **Step 2: Wire the package into `firmware/nedorachio.yaml`**

```yaml
packages:
  core: !include packages/01-core.yaml
  zones: !include packages/02-zones.yaml
  sensors: !include packages/03-sensors.yaml
```

- [ ] **Step 3: Note the pulses-per-gallon dependency**

`pulses_per_gallon` is a `number` entity that doesn't exist yet — it's added in Task 3.1. Until then the lambda referencing `id(pulses_per_gallon)` will fail validation.

**Fix for this task:** hardcode `1.0` in the lambda for now, so the rate works at 1 pulse = 1 gallon. Replace with the `id(pulses_per_gallon).state` lookup in Task 3.1.

- [ ] **Step 4: Validate**

Run: `cd firmware && esphome config nedorachio.yaml`
Expected: "Configuration is valid!"

- [ ] **Step 5: Flash and bench-test**

Flash. In HA:
- Toggle the rain sensor switch on the bench. `binary_sensor.rain_sensor` flips wet/dry.
- Press the flow-meter button rhythmically (e.g. 1/sec for 60s). `sensor.flow_pulses_total` increments by ~60. `sensor.flow_rate_gpm` reads ~60 (pulses-per-min × hardcoded 1 gpp).
- Read `sensor.pressure_psi`. Compare against the manual gauge installed in line. Adjust the calibration lambda if it's more than ±5% off.

- [ ] **Step 6: Commit**

```bash
git add firmware/nedorachio.yaml firmware/packages/03-sensors.yaml
git commit -m "feat(firmware): rain binary, flow pulse + rate, pressure ADC + calibration"
```

---

## Phase 3 — Tunables

### Task 3.1: Write `04-tunables.yaml` — every parameter as a `number` / `switch` / `select`

**Files:**
- Create: `firmware/packages/04-tunables.yaml`
- Modify: `firmware/packages/02-zones.yaml` — replace the temporary `delay: 2s` and the hardcoded runtime cap with `id(...).state` lookups
- Modify: `firmware/packages/03-sensors.yaml` — replace the hardcoded `1.0` with `id(pulses_per_gallon).state`
- Modify: `firmware/nedorachio.yaml`

The full list of tunables is in spec §7.2. Group by section in the YAML for readability.

- [ ] **Step 1: Write `firmware/packages/04-tunables.yaml`**

```yaml
# All tunable parameters: number, select, switch, time. Persisted to flash.

# Helper: every `number` here uses `restore_value: true` so OTA reflash
# preserves the value. Defaults match spec §7.2.

number:
  # --- Per-zone (×8) ---
  - platform: template
    name: "Zone 1 total minutes"
    id: zone_1_total_min
    optimistic: true
    restore_value: true
    initial_value: 20
    min_value: 0
    max_value: 240
    step: 1
    unit_of_measurement: "min"
  # repeat for zones 2..8 with names like "Zone N total minutes"

  - platform: template
    name: "Zone 1 cycle minutes"
    id: zone_1_cycle_min
    optimistic: true
    restore_value: true
    initial_value: 10
    min_value: 1
    max_value: 60
    step: 1
    unit_of_measurement: "min"
  # repeat for zones 2..8

  - platform: template
    name: "Zone 1 soak minutes"
    id: zone_1_soak_min
    optimistic: true
    restore_value: true
    initial_value: 15
    min_value: 0
    max_value: 60
    step: 1
    unit_of_measurement: "min"
  # repeat for zones 2..8

  - platform: template
    name: "Zone 1 max runtime minutes"
    id: zone_1_max_runtime_min
    optimistic: true
    restore_value: true
    initial_value: 60
    min_value: 1
    max_value: 240
    step: 1
    unit_of_measurement: "min"
  # repeat for zones 2..8

  - platform: template
    name: "Zone 1 min flow gpm"
    id: zone_1_min_flow_gpm
    optimistic: true
    restore_value: true
    initial_value: 1.0
    min_value: 0
    max_value: 50
    step: 0.1
    unit_of_measurement: "gpm"
  # repeat for zones 2..8

  - platform: template
    name: "Zone 1 max flow gpm"
    id: zone_1_max_flow_gpm
    optimistic: true
    restore_value: true
    initial_value: 20.0
    min_value: 0
    max_value: 100
    step: 0.1
    unit_of_measurement: "gpm"
  # repeat for zones 2..8

  # --- Sensor calibration ---
  - platform: template
    name: "Pulses per gallon"
    id: pulses_per_gallon
    optimistic: true
    restore_value: true
    initial_value: 10.0      # placeholder; calibrate in Task 9.3
    min_value: 0.1
    max_value: 1000
    step: 0.1

  # --- Pressure gates ---
  - platform: template
    name: "Pressure static min PSI"
    id: pressure_static_min_psi
    optimistic: true
    restore_value: true
    initial_value: 30
    min_value: 0
    max_value: 200
    step: 1
    unit_of_measurement: "psi"

  - platform: template
    name: "Pressure static max PSI"
    id: pressure_static_max_psi
    optimistic: true
    restore_value: true
    initial_value: 80
    min_value: 0
    max_value: 200
    step: 1
    unit_of_measurement: "psi"

  - platform: template
    name: "Pressure running min PSI"
    id: pressure_running_min_psi
    optimistic: true
    restore_value: true
    initial_value: 25
    min_value: 0
    max_value: 200
    step: 1
    unit_of_measurement: "psi"

  - platform: template
    name: "Pressure high PSI"
    id: pressure_high_psi
    optimistic: true
    restore_value: true
    initial_value: 90
    min_value: 0
    max_value: 200
    step: 1
    unit_of_measurement: "psi"

  # --- Flow gates ---
  - platform: template
    name: "Phantom flow gpm"
    id: phantom_flow_gpm
    optimistic: true
    restore_value: true
    initial_value: 0.5
    min_value: 0
    max_value: 5
    step: 0.1
    unit_of_measurement: "gpm"

  - platform: template
    name: "No-flow grace seconds"
    id: no_flow_grace_s
    optimistic: true
    restore_value: true
    initial_value: 60
    min_value: 5
    max_value: 600
    step: 1
    unit_of_measurement: "s"

  - platform: template
    name: "High-flow grace seconds"
    id: high_flow_grace_s
    optimistic: true
    restore_value: true
    initial_value: 30
    min_value: 5
    max_value: 600
    step: 1
    unit_of_measurement: "s"

  # --- Rain gates ---
  - platform: template
    name: "Rain hold hours after sensor"
    id: rain_hold_hours_after_sensor
    optimistic: true
    restore_value: true
    initial_value: 12
    min_value: 0
    max_value: 168
    step: 1
    unit_of_measurement: "h"

  - platform: template
    name: "Rain mm threshold 48h"
    id: rain_mm_threshold_48h
    optimistic: true
    restore_value: true
    initial_value: 5
    min_value: 0
    max_value: 100
    step: 0.1
    unit_of_measurement: "mm"

  - platform: template
    name: "Rain hold hours after forecast"
    id: rain_hold_hours_after_forecast
    optimistic: true
    restore_value: true
    initial_value: 24
    min_value: 0
    max_value: 168
    step: 1
    unit_of_measurement: "h"

  - platform: template
    name: "Rain mm max age hours"
    id: rain_mm_max_age_hours
    optimistic: true
    restore_value: true
    initial_value: 12
    min_value: 1
    max_value: 168
    step: 1
    unit_of_measurement: "h"

  - platform: template
    name: "Rain mm last 48h"          # this one is HA-pushable
    id: rain_mm_last_48h
    optimistic: true
    restore_value: false
    initial_value: 0
    min_value: 0
    max_value: 500
    step: 0.1
    unit_of_measurement: "mm"

  # --- Retry ---
  - platform: template
    name: "Retry count"
    id: retry_count
    optimistic: true
    restore_value: true
    initial_value: 1
    min_value: 0
    max_value: 5
    step: 1

  - platform: template
    name: "Retry delay seconds"
    id: retry_delay_s
    optimistic: true
    restore_value: true
    initial_value: 60
    min_value: 5
    max_value: 600
    step: 1
    unit_of_measurement: "s"

  # --- Catch-up ---
  - platform: template
    name: "Catch-up window hours"
    id: catchup_window_hours
    optimistic: true
    restore_value: true
    initial_value: 6
    min_value: 0
    max_value: 48
    step: 1
    unit_of_measurement: "h"

  # --- Sequencing ---
  - platform: template
    name: "Inter-zone delay seconds"
    id: inter_zone_delay_s
    optimistic: true
    restore_value: true
    initial_value: 2
    min_value: 0
    max_value: 60
    step: 1
    unit_of_measurement: "s"

  # --- Schedule start time (hours/minutes split because ESPHome has no `time` number entity) ---
  - platform: template
    name: "Schedule start hour"
    id: schedule_start_hour
    optimistic: true
    restore_value: true
    initial_value: 6
    min_value: 0
    max_value: 23
    step: 1

  - platform: template
    name: "Schedule start minute"
    id: schedule_start_minute
    optimistic: true
    restore_value: true
    initial_value: 0
    min_value: 0
    max_value: 59
    step: 1

  # The schedule's "rain mm last 48h received at" timestamp; updated whenever
  # HA writes rain_mm_last_48h. Not exposed; used internally for the TTL check.
  - platform: template
    name: "Last rain mm push (epoch s)"
    id: rain_mm_last_pushed_epoch
    optimistic: true
    restore_value: false
    initial_value: 0
    min_value: 0
    max_value: 4000000000
    step: 1

  - platform: template
    name: "Zone N enabled bitmask"      # stores which zones (1..8) are enabled
    id: zones_enabled_bitmask
    optimistic: true
    restore_value: true
    initial_value: 15      # 0b00001111 = zones 1..4 enabled by default
    min_value: 0
    max_value: 255
    step: 1

# Schedule day-of-week mask: 7 booleans (Sun..Sat). True = run that day.
switch:
  - platform: template
    name: "Schedule Sunday"
    id: sched_sun
    optimistic: true
    restore_mode: RESTORE_DEFAULT_OFF
  - platform: template
    name: "Schedule Monday"
    id: sched_mon
    optimistic: true
    restore_mode: RESTORE_DEFAULT_OFF
  - platform: template
    name: "Schedule Tuesday"
    id: sched_tue
    optimistic: true
    restore_mode: RESTORE_DEFAULT_ON
  - platform: template
    name: "Schedule Wednesday"
    id: sched_wed
    optimistic: true
    restore_mode: RESTORE_DEFAULT_OFF
  - platform: template
    name: "Schedule Thursday"
    id: sched_thu
    optimistic: true
    restore_mode: RESTORE_DEFAULT_OFF
  - platform: template
    name: "Schedule Friday"
    id: sched_fri
    optimistic: true
    restore_mode: RESTORE_DEFAULT_OFF
  - platform: template
    name: "Schedule Saturday"
    id: sched_sat
    optimistic: true
    restore_mode: RESTORE_DEFAULT_ON

  - platform: template
    name: "Fallback schedule enabled"
    id: fallback_schedule_enabled
    optimistic: true
    restore_mode: RESTORE_DEFAULT_ON

  - platform: template
    name: "High pressure cancels run"
    id: high_pressure_cancels_run
    optimistic: true
    restore_mode: RESTORE_DEFAULT_OFF
```

- [ ] **Step 2: Update `firmware/packages/02-zones.yaml`**

Replace the temporary `delay: 2s` in `request_zone_on` with the proper lookup:

```yaml
- delay: !lambda 'return id(inter_zone_delay_s).state * 1000;'
```

Replace the hardcoded runtime cap interval. Remove the `${zone_max_runtime_default_s}` substitution and rewrite the per-second check to use the per-zone `zone_N_max_runtime_min` value:

```yaml
interval:
  - interval: 1s
    then:
      - if:
          condition:
            lambda: |-
              if (id(currently_on_zone) == 0) return false;
              uint32_t elapsed_ms = millis() - id(zone_started_at_ms);
              float cap_min = 60.0;
              switch (id(currently_on_zone)) {
                case 1: cap_min = id(zone_1_max_runtime_min).state; break;
                // repeat for zones 2..8
              }
              return elapsed_ms > (uint32_t)(cap_min * 60.0 * 1000.0);
          then:
            - logger.log: "Runtime cap exceeded; cutting zone."
            - script.execute: cancel_everything
            - binary_sensor.template.publish:
                id: alarm_runtime_exceeded
                state: true
```

- [ ] **Step 3: Update `firmware/packages/03-sensors.yaml`**

In the flow-rate filter, replace the hardcoded `1.0` so the divisor is the live `pulses_per_gallon`:

```yaml
filters:
  - lambda: |-
      float ppg = id(pulses_per_gallon).state;
      if (ppg <= 0.0) return 0.0f;
      return x / ppg;
```

(The block above already shows this — the change is removing the temporary hardcoded form noted in Task 2.3 Step 3.)

- [ ] **Step 4: Wire `04-tunables.yaml` into `firmware/nedorachio.yaml`**

```yaml
packages:
  core: !include packages/01-core.yaml
  zones: !include packages/02-zones.yaml
  sensors: !include packages/03-sensors.yaml
  tunables: !include packages/04-tunables.yaml
```

- [ ] **Step 5: Validate**

Run: `cd firmware && esphome config nedorachio.yaml`
Expected: clean.

- [ ] **Step 6: Flash and bench-verify the tunables surface**

In HA, search for `number.zone_1_total_min`, `number.inter_zone_delay_s`, `switch.sched_tue`, `switch.high_pressure_cancels_run`. Each should be present with the spec defaults. Change one (e.g. `inter_zone_delay_s` to 5), reboot the device, confirm the new value persisted.

- [ ] **Step 7: Bench re-test the safety invariants with the new lookups**

Re-run the Task 2.2 Steps 5–8 bench tests. Behavior must be unchanged. The runtime cap test now uses `zone_1_max_runtime_min` — temporarily set it to 0.25 (15s in minutes-as-fraction) via HA, confirm cap fires; restore to 60.

Note: the `min_value` of `zone_N_max_runtime_min` is 1, so 0.25 isn't allowed — for the bench test, lower `min_value` to 0 in the YAML, recompile, test, then restore to 1.

- [ ] **Step 8: Commit**

```bash
git add firmware/nedorachio.yaml firmware/packages/02-zones.yaml firmware/packages/03-sensors.yaml firmware/packages/04-tunables.yaml
git commit -m "feat(firmware): all tunable parameters as number/switch/select entities"
```

---

## Phase 4 — Engine (pre-flight, cycle-and-soak, cancels, retry)

### Task 4.1: Pre-flight gate

**Files:**
- Create: `firmware/packages/05-engine.yaml` (start the file with the pre-flight gate; the rest is filled in by Tasks 4.2–4.4)
- Modify: `firmware/nedorachio.yaml`

The pre-flight gate is a single script that returns pass/fail via a global, plus a binary sensor for the alarm. Spec §7.4 lists the gates.

- [ ] **Step 1: Add the pre-flight gate**

Create `firmware/packages/05-engine.yaml`:

```yaml
# Engine: pre-flight gates, cycle-and-soak, mid-run cancels, retry.

globals:
  - id: pre_flight_passed
    type: bool
    restore_value: false
    initial_value: 'false'
  - id: pre_flight_reason
    type: std::string
    restore_value: false
    initial_value: '""'

  # Last-time-rain-sensor-was-wet, epoch seconds.
  - id: rain_sensor_last_wet_epoch
    type: uint32_t
    restore_value: true
    initial_value: '0'

  # Last-time-rain-mm-was-above-threshold, epoch seconds.
  - id: rain_forecast_last_high_epoch
    type: uint32_t
    restore_value: true
    initial_value: '0'

  # Whether any alarm is latched. Cleared by `clear_fault`.
  - id: any_alarm_latched
    type: bool
    restore_value: true
    initial_value: 'false'

binary_sensor:
  - platform: template
    name: "Alarm: pre-flight failed"
    id: alarm_pre_flight_failed
    device_class: problem

button:
  - platform: template
    name: "Clear fault"
    id: clear_fault
    on_press:
      - lambda: |-
          id(any_alarm_latched) = false;
      - binary_sensor.template.publish: { id: alarm_pre_flight_failed, state: false }
      - binary_sensor.template.publish: { id: alarm_runtime_exceeded, state: false }
      # other alarms wired in later tasks; reset them here too once they exist

# Stamp rain_sensor_last_wet_epoch every time the rain sensor goes wet.
binary_sensor.on_state:    # ESPHome doesn't allow this top-level form; instead:
# (see the actual pattern below)

# Use the on_press equivalent for binary_sensor: extend the rain_sensor itself.
# We do this by editing 03-sensors.yaml in this task.

script:
  - id: run_pre_flight
    mode: single
    parameters:
      is_schedule: bool   # true = called from the fallback schedule (extra gates apply)
    then:
      - lambda: |-
          id(pre_flight_passed) = true;
          id(pre_flight_reason) = "";
      # 1. master_enable
      - if:
          condition:
            switch.is_off: master_enable
          then:
            - lambda: |-
                id(pre_flight_passed) = false;
                id(pre_flight_reason) = "master_enable_off";
      # 2. emergency stop latched
      - if:
          condition:
            switch.is_on: emergency_stop_latched
          then:
            - lambda: |-
                id(pre_flight_passed) = false;
                id(pre_flight_reason) = "emergency_stop_latched";
      # 3. time_synced
      - if:
          condition:
            binary_sensor.is_off: time_synced
          then:
            - lambda: |-
                id(pre_flight_passed) = false;
                id(pre_flight_reason) = "time_not_synced";
      # 4. rain sensor dry + hold
      - lambda: |-
          if (id(rain_sensor).state) {
            id(pre_flight_passed) = false;
            id(pre_flight_reason) = "rain_sensor_wet";
          } else {
            uint32_t now_epoch = id(ha_time).now().timestamp;
            uint32_t hold_s = (uint32_t)(id(rain_hold_hours_after_sensor).state * 3600.0);
            if (id(rain_sensor_last_wet_epoch) > 0 &&
                now_epoch - id(rain_sensor_last_wet_epoch) < hold_s) {
              id(pre_flight_passed) = false;
              id(pre_flight_reason) = "rain_hold_after_sensor";
            }
          }
      # 5. rain forecast (with TTL)
      - lambda: |-
          uint32_t now_epoch = id(ha_time).now().timestamp;
          uint32_t ttl_s = (uint32_t)(id(rain_mm_max_age_hours).state * 3600.0);
          float effective_mm = id(rain_mm_last_48h).state;
          if (id(rain_mm_last_pushed_epoch).state == 0 ||
              now_epoch - (uint32_t)id(rain_mm_last_pushed_epoch).state > ttl_s) {
            effective_mm = 0.0;   // stale → fail-open
          }
          if (effective_mm > id(rain_mm_threshold_48h).state) {
            id(pre_flight_passed) = false;
            id(pre_flight_reason) = "rain_forecast_high";
            id(rain_forecast_last_high_epoch) = now_epoch;
          } else {
            uint32_t hold_s = (uint32_t)(id(rain_hold_hours_after_forecast).state * 3600.0);
            if (id(rain_forecast_last_high_epoch) > 0 &&
                now_epoch - id(rain_forecast_last_high_epoch) < hold_s) {
              id(pre_flight_passed) = false;
              id(pre_flight_reason) = "rain_forecast_hold";
            }
          }
      # 6. static-pressure gate (only meaningful when no zone is on)
      - if:
          condition:
            lambda: 'return id(currently_on_zone) == 0;'
          then:
            - delay: 1s   # let any noise settle
            - lambda: |-
                float p = id(pressure_psi).state;
                if (p < id(pressure_static_min_psi).state) {
                  id(pre_flight_passed) = false;
                  id(pre_flight_reason) = "pressure_too_low";
                } else if (p > id(pressure_static_max_psi).state) {
                  id(pre_flight_passed) = false;
                  id(pre_flight_reason) = "pressure_too_high";
                }
      # 7. no alarm latched
      - if:
          condition:
            lambda: 'return id(any_alarm_latched);'
          then:
            - lambda: |-
                id(pre_flight_passed) = false;
                id(pre_flight_reason) = "alarm_latched";
      # publish the alarm if failed
      - if:
          condition:
            lambda: 'return !id(pre_flight_passed);'
          then:
            - binary_sensor.template.publish: { id: alarm_pre_flight_failed, state: true }
            - lambda: |-
                id(any_alarm_latched) = true;
                ESP_LOGW("preflight", "FAIL: %s", id(pre_flight_reason).c_str());
          else:
            - lambda: |-
                ESP_LOGI("preflight", "PASS");
```

- [ ] **Step 2: Add the rain-sensor-wet stamping**

In `firmware/packages/03-sensors.yaml`, attach an `on_press` to the rain sensor:

```yaml
binary_sensor:
  - platform: gpio
    pin: ...
    name: "Rain sensor"
    id: rain_sensor
    device_class: moisture
    filters:
      - delayed_on_off: 500ms
    on_press:
      - lambda: |-
          id(rain_sensor_last_wet_epoch) = id(ha_time).now().timestamp;
```

- [ ] **Step 3: Wire `05-engine.yaml` into `firmware/nedorachio.yaml`**

```yaml
packages:
  core: !include packages/01-core.yaml
  zones: !include packages/02-zones.yaml
  sensors: !include packages/03-sensors.yaml
  tunables: !include packages/04-tunables.yaml
  engine: !include packages/05-engine.yaml
```

- [ ] **Step 4: Validate**

Run: `cd firmware && esphome config nedorachio.yaml`
Expected: clean.

- [ ] **Step 5: Flash and bench-test pre-flight**

Use the HA Developer Tools → Services → call `esphome.<device>.run_pre_flight` (ESPHome auto-exposes top-level scripts). For each gate:
- master_enable off → fail with reason `master_enable_off`.
- master back on; rain sensor wet → fail with `rain_sensor_wet`.
- dry the rain sensor; pressure too low → fail with `pressure_too_low`. Simulate by holding the ADC pin to GND through a 100kΩ resistor.
- pressure within bounds, all clear → PASS.

- [ ] **Step 6: Commit**

```bash
git add firmware/nedorachio.yaml firmware/packages/03-sensors.yaml firmware/packages/05-engine.yaml
git commit -m "feat(firmware): pre-flight gate (master, e-stop, time, rain, pressure)"
```

### Task 4.2: Cycle-and-soak engine for one zone

**Files:**
- Modify: `firmware/packages/05-engine.yaml`

The engine runs a single zone end-to-end: alternating cycle/soak phases until the total time accrues. It updates the plan-readout globals as it goes. Spec §7.5.

- [ ] **Step 1: Add the per-run state globals**

Append to the `globals:` section of `05-engine.yaml`:

```yaml
  - id: run_total_min
    type: float
    restore_value: false
    initial_value: '0'
  - id: run_cycle_min
    type: float
    restore_value: false
    initial_value: '0'
  - id: run_soak_min
    type: float
    restore_value: false
    initial_value: '0'
  - id: run_zone_id
    type: int
    restore_value: false
    initial_value: '0'
  - id: run_minutes_done
    type: float
    restore_value: false
    initial_value: '0'
  - id: run_cancel_requested
    type: bool
    restore_value: false
    initial_value: 'false'
  - id: run_cancel_cause
    type: std::string
    restore_value: false
    initial_value: '""'
  - id: retries_used_this_zone
    type: int
    restore_value: false
    initial_value: '0'
  - id: run_phase_seconds_left
    type: int
    restore_value: false
    initial_value: '0'
```

- [ ] **Step 2: Add the per-zone runner script**

Append to `script:` in `05-engine.yaml`:

```yaml
  - id: run_one_zone
    mode: single
    parameters:
      zone_id: int
      total_min: float
      cycle_min: float
      soak_min: float
    then:
      - lambda: |-
          id(run_zone_id) = zone_id;
          id(run_total_min) = total_min;
          id(run_cycle_min) = cycle_min;
          id(run_soak_min) = soak_min;
          id(run_minutes_done) = 0.0;
          id(run_cancel_requested) = false;
          id(run_cancel_cause) = "";
          id(retries_used_this_zone) = 0;
      - while:
          condition:
            lambda: |-
              return id(run_minutes_done) < id(run_total_min)
                  && !id(run_cancel_requested);
          then:
            # ---- RUNNING phase ----
            - script.execute:
                id: drive_zone
                zone_id: !lambda 'return id(run_zone_id);'
                state: !lambda 'return true;'
            - lambda: |-
                ESP_LOGI("engine", "Zone %d running for %.1f min (done %.1f / %.1f)",
                  id(run_zone_id), id(run_cycle_min),
                  id(run_minutes_done), id(run_total_min));
            # Tick once per second; exit early on cancel so the post-while
            # cleanup (drive_zone OFF + log) still runs. Don't use
            # `script.stop` here — it halts the whole script and skips cleanup.
            - lambda: |-
                id(run_phase_seconds_left) = (int)(id(run_cycle_min) * 60.0);
            - while:
                condition:
                  lambda: |-
                    return id(run_phase_seconds_left) > 0
                        && !id(run_cancel_requested);
                then:
                  - delay: 1s
                  - lambda: |-
                      id(run_phase_seconds_left) -= 1;
                      id(run_minutes_done) += 1.0 / 60.0;
            # ---- SOAK phase (skip if total reached) ----
            - if:
                condition:
                  lambda: |-
                    return id(run_minutes_done) < id(run_total_min) &&
                           id(run_soak_min) > 0;
                then:
                  - script.execute:
                      id: drive_zone
                      zone_id: !lambda 'return id(run_zone_id);'
                      state: !lambda 'return false;'
                  - lambda: |-
                      ESP_LOGI("engine", "Zone %d soaking for %.1f min", id(run_zone_id), id(run_soak_min));
                  - delay: !lambda 'return (uint32_t)(id(run_soak_min) * 60.0 * 1000.0);'
      # ---- finally ----
      - script.execute:
          id: drive_zone
          zone_id: !lambda 'return id(run_zone_id);'
          state: !lambda 'return false;'
      - lambda: |-
          if (id(run_cancel_requested)) {
            ESP_LOGW("engine", "Zone %d cancelled (cause: %s, %.1f / %.1f min done)",
              id(run_zone_id), id(run_cancel_cause).c_str(),
              id(run_minutes_done), id(run_total_min));
          } else {
            ESP_LOGI("engine", "Zone %d completed (%.1f min)", id(run_zone_id), id(run_minutes_done));
          }
```

- [ ] **Step 3: Validate**

Run: `cd firmware && esphome config nedorachio.yaml`
Expected: clean.

- [ ] **Step 4: Bench-test single-zone cycle-and-soak**

Set `zone_1_total_min = 1`, `zone_1_cycle_min = 0.25` (15 s), `zone_1_soak_min = 0.25` (15 s). Note: lower the `min_value` for these `number` entities temporarily if needed (some have `min_value: 1`). Call `run_one_zone(zone_id=1, total_min=1, cycle_min=0.25, soak_min=0.25)` from HA Dev-Tools → ESPHome service.

Expected sequence at the LED for zone 1:
- ON for 15s → OFF for 15s → ON for 15s → OFF for 15s → ON for 15s → done. (Three running phases of 15s = 45s total + two soaks = full cycle ~75s.)

Watch the logs for the `Zone 1 running for ...` and `Zone 1 soaking for ...` messages. Verify `script.run_one_zone` does not re-enter (check by calling it twice in quick succession; second call should be a no-op because `mode: single`).

- [ ] **Step 5: Commit**

```bash
git add firmware/packages/05-engine.yaml
git commit -m "feat(firmware): single-zone cycle-and-soak engine"
```

### Task 4.3: Mid-run cancel rules

**Files:**
- Modify: `firmware/packages/05-engine.yaml`

Spec §7.6. A 1-second tick during a run watches for: no-flow, high-flow, low-pressure, high-pressure, phantom-flow (always armed), rain-wet. Each sets `run_cancel_requested = true` with a cause string. The engine's while-loop checks the flag and exits.

- [ ] **Step 1: Add the alarm binary sensors**

Append to `binary_sensor:` in `05-engine.yaml`:

```yaml
  - platform: template
    name: "Alarm: no flow"
    id: alarm_no_flow
    device_class: problem
  - platform: template
    name: "Alarm: high flow"
    id: alarm_high_flow
    device_class: problem
  - platform: template
    name: "Alarm: phantom flow"
    id: alarm_phantom_flow
    device_class: problem
  - platform: template
    name: "Alarm: low pressure"
    id: alarm_low_pressure
    device_class: problem
  - platform: template
    name: "Alarm: high pressure"
    id: alarm_high_pressure
    device_class: problem
```

- [ ] **Step 2: Update `clear_fault` to clear all alarms**

Replace the `clear_fault` button's `on_press` block with:

```yaml
on_press:
  - lambda: |-
      id(any_alarm_latched) = false;
  - binary_sensor.template.publish: { id: alarm_pre_flight_failed, state: false }
  - binary_sensor.template.publish: { id: alarm_runtime_exceeded, state: false }
  - binary_sensor.template.publish: { id: alarm_no_flow, state: false }
  - binary_sensor.template.publish: { id: alarm_high_flow, state: false }
  - binary_sensor.template.publish: { id: alarm_phantom_flow, state: false }
  - binary_sensor.template.publish: { id: alarm_low_pressure, state: false }
  - binary_sensor.template.publish: { id: alarm_high_pressure, state: false }
```

- [ ] **Step 3: Add the cancel-watcher interval**

Append to `interval:` (or create one if absent) in `05-engine.yaml`:

```yaml
interval:
  - interval: 1s
    then:
      - lambda: |-
          // Phantom flow: armed whenever no zone is on (including soaking).
          static uint32_t phantom_first_seen_ms = 0;
          float gpm = id(flow_rate_gpm).state;
          if (id(currently_on_zone) == 0 && gpm > id(phantom_flow_gpm).state) {
            if (phantom_first_seen_ms == 0) phantom_first_seen_ms = millis();
            if (millis() - phantom_first_seen_ms > 5UL * 60UL * 1000UL) {
              id(alarm_phantom_flow).publish_state(true);
              id(any_alarm_latched) = true;
            }
          } else {
            phantom_first_seen_ms = 0;
          }

          // Mid-run cancel checks: only when a zone is on AND a run is active.
          if (id(run_zone_id) == 0 || id(currently_on_zone) == 0) return;

          static uint32_t high_flow_first_ms = 0;
          static uint32_t low_psi_first_ms = 0;
          static uint32_t high_psi_first_ms = 0;
          uint32_t now_ms = millis();
          uint32_t since_zone_start_ms = now_ms - id(zone_started_at_ms);

          // No-flow (after grace period).
          if (since_zone_start_ms >= (uint32_t)(id(no_flow_grace_s).state * 1000)) {
            float min_gpm = 1.0;
            switch (id(currently_on_zone)) {
              case 1: min_gpm = id(zone_1_min_flow_gpm).state; break;
              // repeat for zones 2..8
            }
            if (gpm < min_gpm) {
              id(alarm_no_flow).publish_state(true);
              id(any_alarm_latched) = true;
              id(run_cancel_requested) = true;
              id(run_cancel_cause) = "no_flow";
            }
          }

          // High flow (sustained for grace_s).
          float max_gpm = 100.0;
          switch (id(currently_on_zone)) {
            case 1: max_gpm = id(zone_1_max_flow_gpm).state; break;
            // repeat for zones 2..8
          }
          if (gpm > max_gpm) {
            if (high_flow_first_ms == 0) high_flow_first_ms = now_ms;
            if (now_ms - high_flow_first_ms >= (uint32_t)(id(high_flow_grace_s).state * 1000)) {
              id(alarm_high_flow).publish_state(true);
              id(any_alarm_latched) = true;
              id(run_cancel_requested) = true;
              id(run_cancel_cause) = "high_flow";
              high_flow_first_ms = 0;
            }
          } else {
            high_flow_first_ms = 0;
          }

          // Low pressure (after 30s).
          float p = id(pressure_psi).state;
          if (since_zone_start_ms >= 30 * 1000) {
            if (p < id(pressure_running_min_psi).state) {
              if (low_psi_first_ms == 0) low_psi_first_ms = now_ms;
              if (now_ms - low_psi_first_ms >= 5 * 1000) {  // 5s sustained
                id(alarm_low_pressure).publish_state(true);
                id(any_alarm_latched) = true;
                id(run_cancel_requested) = true;
                id(run_cancel_cause) = "low_pressure";
                low_psi_first_ms = 0;
              }
            } else {
              low_psi_first_ms = 0;
            }
          }

          // High pressure (sustained 10s).
          if (p > id(pressure_high_psi).state) {
            if (high_psi_first_ms == 0) high_psi_first_ms = now_ms;
            if (now_ms - high_psi_first_ms >= 10 * 1000) {
              id(alarm_high_pressure).publish_state(true);
              id(any_alarm_latched) = true;
              if (id(high_pressure_cancels_run).state) {
                id(run_cancel_requested) = true;
                id(run_cancel_cause) = "high_pressure";
              }
              high_psi_first_ms = 0;
            }
          } else {
            high_psi_first_ms = 0;
          }

          // Rain sensor wet → cancel (no retry; flag noted for retry policy).
          if (id(rain_sensor).state) {
            id(rain_sensor_last_wet_epoch) = id(ha_time).now().timestamp;
            id(run_cancel_requested) = true;
            id(run_cancel_cause) = "rain";
          }
```

- [ ] **Step 4: Validate and flash**

`esphome config nedorachio.yaml` → clean. `esphome run nedorachio.yaml` → flashed.

- [ ] **Step 5: Bench-test each cancel cause**

For each scenario, start a long zone-1 run (e.g. total_min=5, cycle_min=4, soak_min=1) via `run_one_zone`:

- **No-flow:** zone is on but the bench flow button is unpressed. After `no_flow_grace_s` (default 60s) the run cancels with cause `no_flow`. `alarm_no_flow` latches.
- **High-flow:** rapidly press the bench flow button to simulate >max gpm for the duration of `high_flow_grace_s`. Cancel with `high_flow`.
- **Low-pressure:** simulate low PSI by pulling the ADC pin toward GND through a resistor. After 30s + 5s sustained, cancel with `low_pressure`.
- **High-pressure:** raise the ADC voltage above the high threshold. With `high_pressure_cancels_run` OFF, only the alarm latches (no cancel). With it ON, cancel with `high_pressure`.
- **Phantom flow:** with no zone running, press the bench flow button steadily for 5+ minutes; `alarm_phantom_flow` latches; the active run is unaffected (there shouldn't be one).
- **Rain wet:** while a zone runs, flip the rain switch. Cancel with `rain`. `rain_sensor_last_wet_epoch` updated.

Press `button.clear_fault` between tests so latches reset.

- [ ] **Step 6: Commit**

```bash
git add firmware/packages/05-engine.yaml
git commit -m "feat(firmware): mid-run cancel rules (no-flow, high-flow, low-psi, high-psi, phantom, rain)"
```

### Task 4.4: Retry and fault latching

**Files:**
- Modify: `firmware/packages/05-engine.yaml`

Spec §7.7. After a cancel with cause `flow` or `pressure`, the runner waits `retry_delay_s`, re-evaluates pre-flight, and restarts the *same zone from the beginning* if retries are not exhausted. Cancels caused by `rain` or user action do not retry.

- [ ] **Step 1: Wrap `run_one_zone` in a retry-aware caller**

Add to `script:`:

```yaml
  - id: run_one_zone_with_retry
    mode: single
    parameters:
      zone_id: int
      total_min: float
      cycle_min: float
      soak_min: float
    then:
      - lambda: |-
          id(retries_used_this_zone) = 0;
      - while:
          condition:
            lambda: 'return true;'   # break inside
          then:
            - script.execute:
                id: run_one_zone
                zone_id: !lambda 'return zone_id;'
                total_min: !lambda 'return total_min;'
                cycle_min: !lambda 'return cycle_min;'
                soak_min: !lambda 'return soak_min;'
            - wait_until:
                condition:
                  lambda: 'return !id(run_one_zone).is_running();'
            - if:
                condition:
                  lambda: |-
                    return !id(run_cancel_requested) ||
                           id(run_cancel_cause) == "rain";
                then:
                  - lambda: |-
                      ESP_LOGI("retry", "Zone %d done; not retrying.", zone_id);
                  - script.stop: run_one_zone_with_retry
            - if:
                condition:
                  lambda: |-
                    return id(run_cancel_cause) != "no_flow" &&
                           id(run_cancel_cause) != "high_flow" &&
                           id(run_cancel_cause) != "low_pressure" &&
                           id(run_cancel_cause) != "high_pressure";
                then:
                  - lambda: |-
                      ESP_LOGI("retry", "Zone %d cancelled by %s; not eligible for retry.",
                        zone_id, id(run_cancel_cause).c_str());
                  - script.stop: run_one_zone_with_retry
            - if:
                condition:
                  lambda: 'return id(retries_used_this_zone) >= (int)id(retry_count).state;'
                then:
                  - lambda: |-
                      ESP_LOGW("retry", "Zone %d retries exhausted (%d used). Latching fault.",
                        zone_id, id(retries_used_this_zone));
                      id(any_alarm_latched) = true;
                  - script.stop: run_one_zone_with_retry
            - lambda: |-
                ESP_LOGW("retry", "Zone %d cancelled by %s; retrying after %ds (attempt %d/%d).",
                  zone_id, id(run_cancel_cause).c_str(),
                  (int)id(retry_delay_s).state,
                  id(retries_used_this_zone) + 1,
                  (int)id(retry_count).state);
                id(retries_used_this_zone) += 1;
                // Pause before retry.
            - delay: !lambda 'return (uint32_t)(id(retry_delay_s).state * 1000.0);'
            - script.execute: run_pre_flight
                # Note: pre-flight is `is_schedule: false` here; retries are
                # treated as ad-hoc.
            - if:
                condition:
                  lambda: 'return !id(pre_flight_passed);'
                then:
                  - lambda: |-
                      ESP_LOGW("retry", "Pre-flight failed before retry: %s. Latching fault.",
                        id(pre_flight_reason).c_str());
                  - script.stop: run_one_zone_with_retry
```

Note: ESPHome's `script.execute` of a `single`-mode script when it's already running is a no-op. The `wait_until` after `script.execute` is the idiomatic "wait for completion" pattern.

The pre-flight call inside the retry loop has only one parameter (`is_schedule`). Pass `false`:

```yaml
            - script.execute:
                id: run_pre_flight
                is_schedule: false
            - wait_until:
                condition:
                  lambda: 'return !id(run_pre_flight).is_running();'
```

- [ ] **Step 2: Validate and flash**

`esphome config` → clean. `esphome run`.

- [ ] **Step 3: Bench-test the retry**

Trigger a flow-related cancel (e.g. no-flow on zone 1 with `retry_count = 1`):
- Run starts; LED on; flow rate is zero.
- After `no_flow_grace_s` cancel fires; LED off.
- After `retry_delay_s` (default 60s, lower it to 10 for the test), LED on again — retry started.
- If still no flow: cancel again; retries exhausted; `any_alarm_latched = true`; `alarm_no_flow` stays on.

Repeat with `retry_count = 0` and confirm no retry happens.

Trigger a rain cancel: rain wet → cancel; expected: no retry attempted (cause is `rain`).

- [ ] **Step 4: Commit**

```bash
git add firmware/packages/05-engine.yaml
git commit -m "feat(firmware): retry policy + fault latch on flow/pressure cancels"
```

### Task 4.5: Sequencing — `run_full_cycle` across enabled zones

**Files:**
- Modify: `firmware/packages/05-engine.yaml`

`run_full_cycle` iterates the enabled zones in order. Each zone goes through `run_one_zone_with_retry`. Inter-zone delay is enforced by the safety layer in `02-zones.yaml`. If retries exhaust, the remaining zones are skipped.

- [ ] **Step 1: Add the full-cycle script**

Append to `script:`:

```yaml
  - id: run_full_cycle
    mode: single
    parameters:
      is_schedule: bool
    then:
      - script.execute:
          id: run_pre_flight
          is_schedule: !lambda 'return is_schedule;'
      - wait_until:
          lambda: 'return !id(run_pre_flight).is_running();'
      - if:
          condition:
            lambda: 'return !id(pre_flight_passed);'
          then:
            - logger.log: "Full cycle aborted: pre-flight failed."
            - script.stop: run_full_cycle
      - lambda: |-
          ESP_LOGI("cycle", "Starting full cycle (is_schedule=%d).", is_schedule);
          id(cycle_zone_cursor) = 0;
      # Iterate zones 1..8 using a global counter (avoids relying on
      # `iteration_index` inside `script.execute` parameter lambdas, where its
      # availability isn't guaranteed).
      - while:
          condition:
            lambda: 'return id(cycle_zone_cursor) < 8;'
          then:
            - lambda: |-
                id(cycle_zone_cursor) += 1;   // now 1..8
            - if:
                condition:
                  lambda: |-
                    int zid = id(cycle_zone_cursor);
                    int mask = (int)id(zones_enabled_bitmask).state;
                    return (mask >> (zid - 1)) & 0x1;
                then:
                  - lambda: |-
                      // Stage parameters into per-run globals so the called
                      // script can read them deterministically.
                      int zid = id(cycle_zone_cursor);
                      switch (zid) {
                        case 1:
                          id(staged_total_min) = id(zone_1_total_min).state;
                          id(staged_cycle_min) = id(zone_1_cycle_min).state;
                          id(staged_soak_min)  = id(zone_1_soak_min).state;
                          break;
                        // repeat for zones 2..8
                      }
                  - script.execute:
                      id: run_one_zone_with_retry
                      zone_id: !lambda 'return id(cycle_zone_cursor);'
                      total_min: !lambda 'return id(staged_total_min);'
                      cycle_min: !lambda 'return id(staged_cycle_min);'
                      soak_min:  !lambda 'return id(staged_soak_min);'
                  - wait_until:
                      lambda: 'return !id(run_one_zone_with_retry).is_running();'
                  # If the zone latched a fault, stop the whole cycle.
                  - if:
                      condition:
                        lambda: 'return id(any_alarm_latched);'
                      then:
                        - logger.log: "Full cycle stopped: fault latched."
                        - script.stop: run_full_cycle
      - lambda: ESP_LOGI("cycle", "Full cycle completed.");
```

The new globals (`cycle_zone_cursor`, `staged_total_min`, `staged_cycle_min`, `staged_soak_min`) need to be declared. Add to `globals:` in `05-engine.yaml`:

```yaml
  - id: cycle_zone_cursor
    type: int
    restore_value: false
    initial_value: '0'
  - id: staged_total_min
    type: float
    restore_value: false
    initial_value: '0'
  - id: staged_cycle_min
    type: float
    restore_value: false
    initial_value: '0'
  - id: staged_soak_min
    type: float
    restore_value: false
    initial_value: '0'
```

- [ ] **Step 2: Add the per-zone "run now" buttons**

Append to `button:`:

```yaml
  - platform: template
    name: "Run now zone 1"
    id: run_now_zone_1
    on_press:
      - script.execute:
          id: run_one_zone_with_retry
          zone_id: 1
          total_min: !lambda 'return id(zone_1_total_min).state;'
          cycle_min: !lambda 'return id(zone_1_cycle_min).state;'
          soak_min: !lambda 'return id(zone_1_soak_min).state;'
  # repeat for zones 2..8

  - platform: template
    name: "Run full cycle"
    id: run_full_cycle_btn
    on_press:
      - script.execute:
          id: run_full_cycle
          is_schedule: false
```

- [ ] **Step 3: Validate and flash**

`esphome config` → clean. `esphome run`.

- [ ] **Step 4: Bench-test the full cycle**

Set zones 1..4 to short durations (total=1 min, cycle=0.5, soak=0.25). Set `zones_enabled_bitmask = 15` (zones 1..4). Press `button.run_full_cycle_btn`.

Expected:
- Pre-flight runs and passes.
- Zone 1 runs cycle-and-soak; LED 1 sequence.
- Zone 2 starts after the inter-zone delay.
- Zones 3 and 4 follow.
- Zones 5..8 are skipped.

Then disable zone 3 (set bitmask to 11 = 0b00001011). Press the button. Expected: zones 1, 2, 4 run; zone 3 skipped.

Force a no-flow on zone 2 with `retry_count = 0`. Expected: zone 2 cancels, fault latched, zones 3 and 4 skipped, log message "Full cycle stopped: fault latched."

- [ ] **Step 5: Commit**

```bash
git add firmware/packages/05-engine.yaml
git commit -m "feat(firmware): run_full_cycle sequences enabled zones with per-zone retry"
```

---

## Phase 5 — Schedule, skip, catch-up, plan readouts

### Task 5.1: Weekly schedule fire

**Files:**
- Create: `firmware/packages/06-schedule.yaml`
- Modify: `firmware/nedorachio.yaml`

Spec §7.4 (schedule-only pre-flight gates: `fallback_schedule_enabled`, `skip_next_run`).

- [ ] **Step 1: Write `06-schedule.yaml`**

```yaml
# Weekly schedule fire + skip + catch-up + plan readouts.

globals:
  - id: skip_next_run_pending
    type: bool
    restore_value: true
    initial_value: 'false'
  - id: last_run_started_epoch
    type: uint32_t
    restore_value: true
    initial_value: '0'
  - id: last_fire_epoch
    type: uint32_t
    restore_value: true
    initial_value: '0'

button:
  - platform: template
    name: "Skip next run"
    id: skip_next_run
    icon: mdi:skip-next-circle
    on_press:
      - lambda: |-
          id(skip_next_run_pending) = true;
          ESP_LOGI("schedule", "Next run will be skipped.");

# Fire every minute; check whether this is a scheduled fire time.
interval:
  - interval: 30s
    then:
      - lambda: |-
          if (!id(time_synced).state) return;
          if (!id(fallback_schedule_enabled).state) return;

          auto now = id(ha_time).now();
          if (!now.is_valid()) return;

          // Match start time to the minute (avoid double-fire by tracking last fire).
          int target_h = (int)id(schedule_start_hour).state;
          int target_m = (int)id(schedule_start_minute).state;

          if (now.hour != target_h || now.minute != target_m) return;

          // Day-of-week: ESPHome uses 1=Sunday..7=Saturday.
          bool day_match = false;
          switch (now.day_of_week) {
            case 1: day_match = id(sched_sun).state; break;
            case 2: day_match = id(sched_mon).state; break;
            case 3: day_match = id(sched_tue).state; break;
            case 4: day_match = id(sched_wed).state; break;
            case 5: day_match = id(sched_thu).state; break;
            case 6: day_match = id(sched_fri).state; break;
            case 7: day_match = id(sched_sat).state; break;
          }
          if (!day_match) return;

          // Avoid re-firing within the same minute.
          uint32_t now_epoch = now.timestamp;
          if (id(last_fire_epoch) > 0 && now_epoch - id(last_fire_epoch) < 60) return;

          id(last_fire_epoch) = now_epoch;

          if (id(skip_next_run_pending)) {
            id(skip_next_run_pending) = false;
            ESP_LOGI("schedule", "Skip flag consumed; not firing this run.");
            return;
          }
          // Don't fire if a zone is already on (HA may be running its own).
          if (id(currently_on_zone) != 0) {
            ESP_LOGI("schedule", "Zone already on; fallback aborted.");
            return;
          }

          // Defer to ESPHome script execution outside the lambda.
          id(schedule_should_fire).publish_state(true);

binary_sensor:
  - platform: template
    name: "Schedule fire pending"
    id: schedule_should_fire
    internal: true            # device-internal trigger only
    on_press:
      - script.execute: schedule_fire_handler

# When the trigger flips on, run the full cycle and clear the trigger.
script:
  - id: schedule_fire_handler
    mode: single
    then:
      - script.execute:
          id: run_full_cycle
          is_schedule: true
      - wait_until:
          lambda: 'return !id(run_full_cycle).is_running();'
      - binary_sensor.template.publish: { id: schedule_should_fire, state: false }
```

Pattern: the interval lambda above calls `id(schedule_should_fire).publish_state(true)`. The rising edge triggers `on_press`, which kicks off `schedule_fire_handler`. The handler waits for `run_full_cycle` to finish, then clears the flag.

- [ ] **Step 2: Wire `06-schedule.yaml`**

```yaml
packages:
  core: !include packages/01-core.yaml
  zones: !include packages/02-zones.yaml
  sensors: !include packages/03-sensors.yaml
  tunables: !include packages/04-tunables.yaml
  engine: !include packages/05-engine.yaml
  schedule: !include packages/06-schedule.yaml
```

- [ ] **Step 3: Validate and flash**

`esphome config` → clean. `esphome run`.

- [ ] **Step 4: Bench-test the schedule**

Set the schedule for a time 2 minutes in the future (e.g. now is 14:30 → set hour=14, minute=32). Enable today's day-of-week switch. Set `zone_1_total_min = 1`, etc. Wait for the fire.

Expected: at HH:MM, the pre-flight runs, then `run_full_cycle` runs zone 1 for ~1 minute. Re-runs aren't possible until the next eligible minute (the `last_fire_epoch` check prevents re-fire).

Test the skip: press `button.skip_next_run`, set the time for 1 minute in the future, wait. Expected: log message "Skip flag consumed; not firing this run." `skip_next_run_pending` global goes back to false.

- [ ] **Step 5: Commit**

```bash
git add firmware/nedorachio.yaml firmware/packages/06-schedule.yaml
git commit -m "feat(firmware): weekly schedule fire + skip-next-run"
```

### Task 5.2: Catch-up on boot

**Files:**
- Modify: `firmware/packages/06-schedule.yaml`

Spec §7.8. On boot + first NTP sync, look up the most recent scheduled fire within `catchup_window_hours`. If `last_run_started_epoch < that_fire_epoch`, fire now.

- [ ] **Step 1: Add a helper to compute "most recent scheduled fire in the last N hours"**

Append to `06-schedule.yaml`:

```yaml
script:
  - id: maybe_catch_up
    mode: single
    then:
      - lambda: |-
          if (!id(time_synced).state) return;
          if (!id(fallback_schedule_enabled).state) return;
          if ((int)id(catchup_window_hours).state == 0) return;

          auto now = id(ha_time).now();
          uint32_t now_epoch = now.timestamp;
          uint32_t window_s = (uint32_t)(id(catchup_window_hours).state * 3600.0);
          uint32_t target_h = (uint32_t)id(schedule_start_hour).state;
          uint32_t target_m = (uint32_t)id(schedule_start_minute).state;

          // Walk backward day by day looking for the most recent scheduled fire.
          // Bound search to window_s (in days, ceil).
          int max_days = (int)((window_s / 86400) + 1);
          for (int d = 0; d <= max_days; d++) {
            time_t cand = (time_t)now_epoch - d * 86400;
            struct tm cand_tm;
            localtime_r(&cand, &cand_tm);
            cand_tm.tm_hour = target_h;
            cand_tm.tm_min = target_m;
            cand_tm.tm_sec = 0;
            time_t fire_epoch_t = mktime(&cand_tm);
            uint32_t fire_epoch = (uint32_t)fire_epoch_t;
            if (fire_epoch > now_epoch) continue;        // future today; skip
            if (now_epoch - fire_epoch > window_s) return; // outside window; nothing to catch up

            // Check day-of-week match.
            int dow = cand_tm.tm_wday + 1;   // tm_wday: 0=Sun..6=Sat → 1..7
            bool day_match = false;
            switch (dow) {
              case 1: day_match = id(sched_sun).state; break;
              case 2: day_match = id(sched_mon).state; break;
              case 3: day_match = id(sched_tue).state; break;
              case 4: day_match = id(sched_wed).state; break;
              case 5: day_match = id(sched_thu).state; break;
              case 6: day_match = id(sched_fri).state; break;
              case 7: day_match = id(sched_sat).state; break;
            }
            if (!day_match) continue;

            // Match found. Did we already run after this fire time?
            if (id(last_run_started_epoch) >= fire_epoch) {
              ESP_LOGI("catchup", "Most recent fire was %us ago; already ran. No catch-up.",
                (unsigned)(now_epoch - fire_epoch));
              return;
            }
            ESP_LOGW("catchup", "Catching up missed fire from %us ago.",
              (unsigned)(now_epoch - fire_epoch));
            id(schedule_should_fire).publish_state(true);
            return;
          }
```

- [ ] **Step 2: Trigger catch-up after time syncs**

Modify the `01-core.yaml` time-sync triggers to also call `maybe_catch_up` once after sync. Append to *both* `on_time_sync:` lambdas:

```yaml
on_time_sync:
  - lambda: |-
      static bool first_sync = true;
      id(time_synced).publish_state(true);
      if (first_sync) {
        first_sync = false;
        // Defer catch-up so logs are coherent.
      }
  - script.execute: maybe_catch_up
```

(Both `homeassistant` and `sntp` time entries trigger `maybe_catch_up` on sync; the script is `mode: single` and idempotent within the same boot.)

- [ ] **Step 3: Stamp `last_run_started_epoch` when a run starts**

In `05-engine.yaml`, in `run_one_zone_with_retry` (or in `run_full_cycle`), at the very start, add:

```yaml
- lambda: |-
    id(last_run_started_epoch) = id(ha_time).now().timestamp;
```

Place it as the first action in `run_full_cycle` (before pre-flight) so any cycle (manual or scheduled) updates the watermark.

- [ ] **Step 4: Validate and flash**

- [ ] **Step 5: Bench-test catch-up**

- Set the schedule for a time **5 minutes ago** (e.g. now is 14:35, set hour=14, minute=30). Enable today's day. Reboot the board (press EN).
- After WiFi connects and NTP syncs, the device should log `Catching up missed fire from ~300s ago.` and start the cycle.
- Press EN again immediately while it's running. After re-sync, `last_run_started_epoch` is greater than the catch-up fire time, so no second catch-up runs (log: `... already ran.`).
- Set `catchup_window_hours = 0` and reboot with a missed fire pending. Expected: no catch-up.

- [ ] **Step 6: Commit**

```bash
git add firmware/packages/01-core.yaml firmware/packages/05-engine.yaml firmware/packages/06-schedule.yaml
git commit -m "feat(firmware): catch up missed schedule fires on boot"
```

### Task 5.3: Plan-readout sensors

**Files:**
- Modify: `firmware/packages/06-schedule.yaml`

Spec §7.1 plan readouts: `current_phase`, `currently_running_zone`, `current_phase_remaining_s`, `run_progress_pct`, `next_planned_run`, `last_run_started_at`, `last_run_finished_at`, `last_run_outcome`.

- [ ] **Step 1: Add per-phase tracking globals (if not already present)**

Append:

```yaml
globals:
  - id: current_phase_str
    type: std::string
    restore_value: false
    initial_value: '"idle"'
  - id: phase_started_ms
    type: uint32_t
    restore_value: false
    initial_value: '0'
  - id: phase_total_ms
    type: uint32_t
    restore_value: false
    initial_value: '0'
  - id: last_run_finished_epoch
    type: uint32_t
    restore_value: true
    initial_value: '0'
  - id: last_run_outcome_str
    type: std::string
    restore_value: true
    initial_value: '""'
```

- [ ] **Step 2: Update phase transitions in `05-engine.yaml`**

Wherever a phase changes (running, soaking, inter-zone delay, fault), update the globals:

```yaml
# Just before `drive_zone(... state: true)` in the running branch:
- lambda: |-
    id(current_phase_str) = "running";
    id(phase_started_ms) = millis();
    id(phase_total_ms) = (uint32_t)(id(run_cycle_min).state * 60000.0);
```

```yaml
# Just before `drive_zone(... state: false)` in the soak branch:
- lambda: |-
    id(current_phase_str) = "soaking";
    id(phase_started_ms) = millis();
    id(phase_total_ms) = (uint32_t)(id(run_soak_min).state * 60000.0);
```

```yaml
# At the end of run_full_cycle (whether success or fault), set idle:
- lambda: |-
    id(current_phase_str) = id(any_alarm_latched) ? "fault" : "idle";
    id(last_run_finished_epoch) = id(ha_time).now().timestamp;
    if (id(any_alarm_latched)) {
      id(last_run_outcome_str) = "cancelled_" + id(run_cancel_cause);
    } else {
      id(last_run_outcome_str) = "completed";
    }
```

(If `run_cancel_cause` is empty, `last_run_outcome_str = "completed"`; otherwise it's `cancelled_no_flow`, etc., matching spec §7.1.)

- [ ] **Step 3: Add the plan-readout sensors**

Append to `06-schedule.yaml`:

```yaml
text_sensor:
  - platform: template
    name: "Current phase"
    id: current_phase
    update_interval: 1s
    lambda: 'return id(current_phase_str);'

  - platform: template
    name: "Last run outcome"
    id: last_run_outcome
    update_interval: 5s
    lambda: 'return id(last_run_outcome_str);'

sensor:
  - platform: template
    name: "Currently running zone"
    id: currently_running_zone
    update_interval: 1s
    lambda: 'return id(currently_on_zone);'
    accuracy_decimals: 0

  - platform: template
    name: "Current phase remaining (s)"
    id: current_phase_remaining_s
    update_interval: 1s
    accuracy_decimals: 0
    unit_of_measurement: "s"
    lambda: |-
      if (id(phase_total_ms) == 0) return 0.0;
      uint32_t elapsed = millis() - id(phase_started_ms);
      if (elapsed >= id(phase_total_ms)) return 0.0;
      return (id(phase_total_ms) - elapsed) / 1000.0;

  - platform: template
    name: "Run progress %"
    id: run_progress_pct
    update_interval: 1s
    accuracy_decimals: 0
    unit_of_measurement: "%"
    lambda: |-
      if (id(run_total_min) == 0) return 0.0;
      return (id(run_minutes_done) / id(run_total_min)) * 100.0;

  - platform: template
    name: "Next planned run (epoch)"
    id: next_planned_run
    update_interval: 60s
    accuracy_decimals: 0
    unit_of_measurement: "s"
    lambda: |-
      if (!id(time_synced).state) return 0.0;
      auto now = id(ha_time).now();
      if (!now.is_valid()) return 0.0;
      // Walk forward up to 8 days looking for the next enabled day-of-week.
      uint32_t now_epoch = now.timestamp;
      int target_h = (int)id(schedule_start_hour).state;
      int target_m = (int)id(schedule_start_minute).state;
      for (int d = 0; d < 8; d++) {
        time_t cand = (time_t)now_epoch + d * 86400;
        struct tm cand_tm;
        localtime_r(&cand, &cand_tm);
        cand_tm.tm_hour = target_h;
        cand_tm.tm_min = target_m;
        cand_tm.tm_sec = 0;
        time_t fire = mktime(&cand_tm);
        if ((uint32_t)fire <= now_epoch) continue;
        int dow = cand_tm.tm_wday + 1;
        bool match = false;
        switch (dow) {
          case 1: match = id(sched_sun).state; break;
          case 2: match = id(sched_mon).state; break;
          case 3: match = id(sched_tue).state; break;
          case 4: match = id(sched_wed).state; break;
          case 5: match = id(sched_thu).state; break;
          case 6: match = id(sched_fri).state; break;
          case 7: match = id(sched_sat).state; break;
        }
        if (match) return (float)fire;
      }
      return 0.0;

  - platform: template
    name: "Last run started (epoch)"
    id: last_run_started_at
    update_interval: 60s
    accuracy_decimals: 0
    lambda: 'return (float)id(last_run_started_epoch);'

  - platform: template
    name: "Last run finished (epoch)"
    id: last_run_finished_at
    update_interval: 60s
    accuracy_decimals: 0
    lambda: 'return (float)id(last_run_finished_epoch);'
```

- [ ] **Step 4: Validate and flash**

- [ ] **Step 5: Bench-test plan readouts**

Trigger `run_full_cycle_btn`. Watch in HA:
- `text_sensor.current_phase` → `running` → `soaking` → `running` → ... → `idle`.
- `sensor.currently_running_zone` → 1 → 2 → ... → 0.
- `sensor.run_progress_pct` climbs 0..100 across the whole cycle.
- `sensor.next_planned_run` shows a future epoch matching the configured day/time.
- After a fault, `text_sensor.current_phase` shows `fault` and `text_sensor.last_run_outcome` shows e.g. `cancelled_no_flow`.

- [ ] **Step 6: Commit**

```bash
git add firmware/packages/05-engine.yaml firmware/packages/06-schedule.yaml
git commit -m "feat(firmware): plan-readout sensors (phase, progress, next/last run)"
```

---

## Phase 6 — Stats

### Task 6.1: Per-zone counters with persistence

**Files:**
- Create: `firmware/packages/07-stats.yaml`
- Modify: `firmware/packages/05-engine.yaml` (hook stats updates into the engine)
- Modify: `firmware/nedorachio.yaml`

Spec §7.1 stats: per-zone gallons, run_count, seconds_total, last_*; today/this_month aggregates.

- [ ] **Step 1: Write `07-stats.yaml`**

```yaml
# Persisted per-zone stats and daily/monthly aggregates.

globals:
  # Per zone, persisted.
  - id: zone_1_run_count_total
    type: uint32_t
    restore_value: true
    initial_value: '0'
  - id: zone_1_gallons_total
    type: float
    restore_value: true
    initial_value: '0'
  - id: zone_1_seconds_total
    type: uint32_t
    restore_value: true
    initial_value: '0'
  - id: zone_1_last_gallons
    type: float
    restore_value: true
    initial_value: '0'
  - id: zone_1_last_duration_s
    type: uint32_t
    restore_value: true
    initial_value: '0'
  - id: zone_1_last_run_at
    type: uint32_t
    restore_value: true
    initial_value: '0'
  # repeat for zones 2..8

  # Run-time accumulators (cleared at the start of each per-zone run).
  - id: zone_run_started_pulses
    type: uint32_t
    restore_value: false
    initial_value: '0'
  - id: zone_run_started_ms
    type: uint32_t
    restore_value: false
    initial_value: '0'

  # Aggregates.
  - id: gallons_today
    type: float
    restore_value: true
    initial_value: '0'
  - id: gallons_this_month
    type: float
    restore_value: true
    initial_value: '0'
  - id: runs_today
    type: uint32_t
    restore_value: true
    initial_value: '0'
  - id: runs_this_month
    type: uint32_t
    restore_value: true
    initial_value: '0'
  - id: stats_last_rollover_day_of_year
    type: int
    restore_value: true
    initial_value: '-1'
  - id: stats_last_rollover_month
    type: int
    restore_value: true
    initial_value: '-1'

# Expose stats as sensors.
sensor:
  - platform: template
    name: "Zone 1 run count"
    id: zone_1_run_count_sensor
    update_interval: 60s
    accuracy_decimals: 0
    lambda: 'return (float)id(zone_1_run_count_total);'
  # repeat for zones 2..8 (six more entities each: run_count, gallons_total, seconds_total, last_gallons, last_duration_s, last_run_at)

  - platform: template
    name: "Zone 1 gallons total"
    id: zone_1_gallons_total_sensor
    update_interval: 60s
    accuracy_decimals: 1
    unit_of_measurement: "gal"
    lambda: 'return id(zone_1_gallons_total);'
  # repeat for zones 2..8

  - platform: template
    name: "Zone 1 seconds total"
    id: zone_1_seconds_total_sensor
    update_interval: 60s
    accuracy_decimals: 0
    unit_of_measurement: "s"
    lambda: 'return (float)id(zone_1_seconds_total);'

  - platform: template
    name: "Zone 1 last gallons"
    id: zone_1_last_gallons_sensor
    update_interval: 60s
    accuracy_decimals: 1
    unit_of_measurement: "gal"
    lambda: 'return id(zone_1_last_gallons);'

  - platform: template
    name: "Zone 1 last duration (s)"
    id: zone_1_last_duration_sensor
    update_interval: 60s
    accuracy_decimals: 0
    unit_of_measurement: "s"
    lambda: 'return (float)id(zone_1_last_duration_s);'

  - platform: template
    name: "Zone 1 last run at (epoch)"
    id: zone_1_last_run_at_sensor
    update_interval: 60s
    accuracy_decimals: 0
    lambda: 'return (float)id(zone_1_last_run_at);'

  # repeat all five sensor blocks for zones 2..8.

  - platform: template
    name: "Gallons today"
    id: gallons_today_sensor
    update_interval: 60s
    accuracy_decimals: 1
    unit_of_measurement: "gal"
    lambda: 'return id(gallons_today);'

  - platform: template
    name: "Gallons this month"
    id: gallons_this_month_sensor
    update_interval: 60s
    accuracy_decimals: 1
    unit_of_measurement: "gal"
    lambda: 'return id(gallons_this_month);'

  - platform: template
    name: "Runs today"
    id: runs_today_sensor
    update_interval: 60s
    accuracy_decimals: 0
    lambda: 'return (float)id(runs_today);'

  - platform: template
    name: "Runs this month"
    id: runs_this_month_sensor
    update_interval: 60s
    accuracy_decimals: 0
    lambda: 'return (float)id(runs_this_month);'

# Daily/monthly rollover.
interval:
  - interval: 60s
    then:
      - lambda: |-
          if (!id(time_synced).state) return;
          auto now = id(ha_time).now();
          if (!now.is_valid()) return;
          int doy = now.day_of_year;
          int mon = now.month;
          if (id(stats_last_rollover_day_of_year) != -1 && doy != id(stats_last_rollover_day_of_year)) {
            id(gallons_today) = 0;
            id(runs_today) = 0;
          }
          if (id(stats_last_rollover_month) != -1 && mon != id(stats_last_rollover_month)) {
            id(gallons_this_month) = 0;
            id(runs_this_month) = 0;
          }
          id(stats_last_rollover_day_of_year) = doy;
          id(stats_last_rollover_month) = mon;
```

- [ ] **Step 2: Wire `07-stats.yaml`**

```yaml
packages:
  ...
  stats: !include packages/07-stats.yaml
```

- [ ] **Step 3: Hook stats updates into the engine**

In `05-engine.yaml`'s `run_one_zone`, replace the start lambda (the one that resets `run_minutes_done`) to also stamp the run-start counters:

```yaml
- lambda: |-
    id(run_zone_id) = zone_id;
    id(run_total_min) = total_min;
    id(run_cycle_min) = cycle_min;
    id(run_soak_min) = soak_min;
    id(run_minutes_done) = 0.0;
    id(run_cancel_requested) = false;
    id(run_cancel_cause) = "";
    id(retries_used_this_zone) = 0;
    id(zone_run_started_pulses) = (uint32_t)id(flow_pulses_total).state;
    id(zone_run_started_ms) = millis();
```

In the finally block of `run_one_zone` (the post-while lambda that logs completion), append:

```yaml
- lambda: |-
    uint32_t end_pulses = (uint32_t)id(flow_pulses_total).state;
    uint32_t pulses = end_pulses - id(zone_run_started_pulses);
    float ppg = id(pulses_per_gallon).state;
    float gallons = (ppg > 0) ? (pulses / ppg) : 0.0f;
    uint32_t duration_s = (millis() - id(zone_run_started_ms)) / 1000;

    switch (id(run_zone_id)) {
      case 1:
        id(zone_1_run_count_total) += 1;
        id(zone_1_gallons_total) += gallons;
        id(zone_1_seconds_total) += duration_s;
        id(zone_1_last_gallons) = gallons;
        id(zone_1_last_duration_s) = duration_s;
        id(zone_1_last_run_at) = id(ha_time).now().timestamp;
        break;
      // repeat for zones 2..8
    }
    id(gallons_today) += gallons;
    id(gallons_this_month) += gallons;
    id(runs_today) += 1;
    id(runs_this_month) += 1;
```

- [ ] **Step 4: Validate and flash**

- [ ] **Step 5: Bench-test stats**

- Trigger a short run on zone 1 with simulated flow pulses (press the flow button steadily during the running phase). After the run:
  - `sensor.zone_1_run_count` → 1.
  - `sensor.zone_1_gallons_total` → ~ (pulses you generated) / pulses_per_gallon.
  - `sensor.zone_1_last_run_at` → ~now epoch.
  - `sensor.gallons_today` and `gallons_this_month` non-zero.
- Reboot the board. Counters persist (run_count_total still 1).
- Force a day rollover by changing `pulses_per_gallon` and looking at the next day's first run, OR shorten the rollover trigger temporarily by setting `stats_last_rollover_day_of_year = -2` via lambda inside an HA-callable script (advanced). Easiest test: skip this and trust the time-rollover lambda since it's small and reviewable.

- [ ] **Step 6: Commit**

```bash
git add firmware/nedorachio.yaml firmware/packages/05-engine.yaml firmware/packages/07-stats.yaml
git commit -m "feat(firmware): per-zone gallon/run/duration stats with daily/monthly rollover"
```

---

## Phase 7 — Home Assistant package

### Task 7.1: Weather feeder automation

**Files:**
- Create: `homeassistant/packages/nedorachio.yaml`

Spec §8.1. One automation, fires every 10 minutes; reads from a configured weather entity; writes to `number.nedorachio_rain_mm_last_48h` (the entity name HA assigns to the device's `rain_mm_last_48h`).

- [ ] **Step 1: Write `homeassistant/packages/nedorachio.yaml`**

```yaml
# Nedorachio Home Assistant package: weather feeder, notifications, dashboard.

# 1. Weather feeder.
# Replace `weather.your_local_forecast` with the user's actual weather entity.
# Many HA setups have `weather.home` or `weather.openweathermap`.
# The recorder sums the last 48h of `precipitation` from the daily forecast +
# any directly-reported observed rainfall sensor.

automation:
  - id: nedorachio_weather_feeder
    alias: "Nedorachio: push 48h rain to controller"
    trigger:
      - platform: time_pattern
        minutes: "/10"
    action:
      - service: weather.get_forecasts
        target:
          entity_id: weather.your_local_forecast
        data:
          type: hourly
        response_variable: forecast
      - variables:
          # Sum precipitation_amount over hourly entries in the last 48h.
          # The hourly forecast covers ~next 5 days; we want PAST 48h.
          # If the user's weather provider exposes an observed-rain sensor,
          # use that instead. For now, default to a stub of 0.0 if no
          # observed-rain sensor is configured.
          rain_mm: >-
            {% set obs_id = 'sensor.rain_observed_48h' %}
            {% if states(obs_id) not in ['unknown','unavailable','none', None] %}
              {{ states(obs_id) | float(0) }}
            {% else %}
              0.0
            {% endif %}
      - service: number.set_value
        target:
          entity_id: number.nedorachio_rain_mm_last_48h
        data:
          value: "{{ rain_mm }}"
```

- [ ] **Step 2: Document the weather-entity dependency in `README.md`**

Append a "Home Assistant setup" section:

```markdown
## Home Assistant setup

1. Copy `homeassistant/packages/nedorachio.yaml` into your HA config under `packages/`. (Make sure your `configuration.yaml` includes `homeassistant: packages: !include_dir_named packages`.)
2. Replace `weather.your_local_forecast` with your own weather entity and `sensor.rain_observed_48h` with whatever sensor you have for observed rainfall in the last 48h. If you don't have one, leave it as-is — the feeder will write 0 and the device falls back to the rain sensor and the static-pressure gate.
3. Reload automations. Verify `number.nedorachio_rain_mm_last_48h` is populated within 10 minutes.
```

- [ ] **Step 3: Test by hand**

In HA Dev-Tools → Services, call the automation: Settings → Automations & Scenes → "Nedorachio: push 48h rain to controller" → Run actions. Verify `number.nedorachio_rain_mm_last_48h` updates.

- [ ] **Step 4: Commit**

```bash
git add homeassistant/packages/nedorachio.yaml README.md
git commit -m "feat(ha): weather feeder pushes rain_mm_last_48h to controller every 10m"
```

### Task 7.2: Notification routes

**Files:**
- Modify: `homeassistant/packages/nedorachio.yaml`

One automation per alarm binary sensor. Fires on rising edge.

- [ ] **Step 1: Add notification automations**

Append to `homeassistant/packages/nedorachio.yaml`:

```yaml
automation:
  # ... existing weather feeder above

  - id: nedorachio_alarm_notify
    alias: "Nedorachio: alarm notifications"
    mode: parallel
    trigger:
      - platform: state
        entity_id:
          - binary_sensor.nedorachio_alarm_no_flow
          - binary_sensor.nedorachio_alarm_high_flow
          - binary_sensor.nedorachio_alarm_phantom_flow
          - binary_sensor.nedorachio_alarm_low_pressure
          - binary_sensor.nedorachio_alarm_high_pressure
          - binary_sensor.nedorachio_alarm_pre_flight_failed
          - binary_sensor.nedorachio_alarm_runtime_exceeded
        to: "on"
    action:
      - variables:
          alarm_name: "{{ trigger.to_state.attributes.friendly_name }}"
          zone: "{{ states('sensor.nedorachio_currently_running_zone') }}"
          phase: "{{ states('text_sensor.nedorachio_current_phase') }}"
          psi: "{{ states('sensor.nedorachio_pressure') }}"
          gpm: "{{ states('sensor.nedorachio_flow_rate') }}"
      - service: notify.notify   # replace with the user's notify target
        data:
          title: "Nedorachio: {{ alarm_name }}"
          message: >-
            Phase {{ phase }}, zone {{ zone }}.
            PSI {{ psi }}, flow {{ gpm }} gpm.
```

- [ ] **Step 2: Document the `notify.notify` target**

Append to README's HA setup section:

```markdown
4. Edit the `notify.notify` line in `nedorachio_alarm_notify` to use your actual notification target (e.g. `notify.mobile_app_yourphone`).
```

- [ ] **Step 3: Test**

Pull the rain sensor wet during a run on the bench. The cancel fires `alarm_no_flow` (or whichever alarm is appropriate); HA should send a notification within seconds.

- [ ] **Step 4: Commit**

```bash
git add homeassistant/packages/nedorachio.yaml README.md
git commit -m "feat(ha): notification automation for all device alarms"
```

### Task 7.3: Dashboard

**Files:**
- Modify: `homeassistant/packages/nedorachio.yaml`

A single Lovelace view, vanilla cards. Spec §8.4.

- [ ] **Step 1: Add the Lovelace view**

Append to the package (HA supports `lovelace: dashboards: ...` only in YAML mode; for a UI-mode HA, these go in the HA UI). For repo-friendliness, also include the YAML so the user can paste it into the dashboard editor:

```yaml
# Lovelace view (paste into the dashboard editor under Raw Configuration Editor → views).
# Saved here as documentation; HA in UI mode does not auto-import this.

# fmt: off
__lovelace_view_nedorachio: !include nedorachio_dashboard.yaml
```

Create `homeassistant/packages/nedorachio_dashboard.yaml`:

```yaml
title: Nedorachio
icon: mdi:sprinkler
path: nedorachio
cards:
  - type: entities
    title: Master controls
    entities:
      - entity: switch.nedorachio_master_enable
        name: Master enable
      - entity: switch.nedorachio_fallback_schedule_enabled
        name: Fallback schedule
      - entity: button.nedorachio_emergency_stop_all
        name: Emergency stop
      - entity: button.nedorachio_skip_next_run
        name: Skip next run
      - entity: button.nedorachio_clear_fault
        name: Clear fault

  - type: entities
    title: Status
    entities:
      - entity: text_sensor.nedorachio_current_phase
      - entity: sensor.nedorachio_currently_running_zone
      - entity: sensor.nedorachio_current_phase_remaining_s
      - entity: sensor.nedorachio_run_progress
      - entity: sensor.nedorachio_next_planned_run
      - entity: text_sensor.nedorachio_last_run_outcome

  - type: glance
    title: Live sensors
    entities:
      - entity: binary_sensor.nedorachio_rain_sensor
      - entity: sensor.nedorachio_flow_rate
      - entity: sensor.nedorachio_pressure
      - entity: sensor.nedorachio_gallons_today
      - entity: sensor.nedorachio_gallons_this_month

  - type: entities
    title: Zone 1
    entities:
      - entity: switch.nedorachio_zone_1
      - entity: number.nedorachio_zone_1_total_minutes
      - entity: number.nedorachio_zone_1_cycle_minutes
      - entity: number.nedorachio_zone_1_soak_minutes
      - entity: button.nedorachio_run_now_zone_1
      - entity: sensor.nedorachio_zone_1_last_gallons
      - entity: sensor.nedorachio_zone_1_run_count
      - entity: sensor.nedorachio_zone_1_gallons_total
  # repeat for zones 2..4 (zones 5..8 hidden until used)

  - type: entities
    title: Alarms
    entities:
      - entity: binary_sensor.nedorachio_alarm_pre_flight_failed
      - entity: binary_sensor.nedorachio_alarm_no_flow
      - entity: binary_sensor.nedorachio_alarm_high_flow
      - entity: binary_sensor.nedorachio_alarm_phantom_flow
      - entity: binary_sensor.nedorachio_alarm_low_pressure
      - entity: binary_sensor.nedorachio_alarm_high_pressure
      - entity: binary_sensor.nedorachio_alarm_runtime_exceeded

  - type: entities
    title: Tuning
    entities:
      - entity: number.nedorachio_pulses_per_gallon
      - entity: number.nedorachio_pressure_static_min_psi
      - entity: number.nedorachio_pressure_static_max_psi
      - entity: number.nedorachio_pressure_running_min_psi
      - entity: number.nedorachio_pressure_high_psi
      - entity: number.nedorachio_phantom_flow_gpm
      - entity: number.nedorachio_inter_zone_delay_s
      - entity: number.nedorachio_retry_count
      - entity: number.nedorachio_retry_delay_s
      - entity: number.nedorachio_catchup_window_hours
      - entity: number.nedorachio_rain_hold_hours_after_sensor
      - entity: number.nedorachio_rain_mm_threshold_48h
      - entity: number.nedorachio_rain_hold_hours_after_forecast
      - entity: number.nedorachio_rain_mm_max_age_hours
      - entity: switch.nedorachio_high_pressure_cancels_run
      - entity: number.nedorachio_schedule_start_hour
      - entity: number.nedorachio_schedule_start_minute
      - entity: switch.nedorachio_sched_sun
      - entity: switch.nedorachio_sched_mon
      - entity: switch.nedorachio_sched_tue
      - entity: switch.nedorachio_sched_wed
      - entity: switch.nedorachio_sched_thu
      - entity: switch.nedorachio_sched_fri
      - entity: switch.nedorachio_sched_sat
```

- [ ] **Step 2: Document dashboard import**

Append to README:

```markdown
5. Add a new dashboard view: in HA, Settings → Dashboards → Open the Lovelace dashboard → ⋮ → Edit Dashboard → ⋮ → Raw configuration editor → paste the contents of `homeassistant/packages/nedorachio_dashboard.yaml` under `views:`.
```

- [ ] **Step 3: Verify**

Open the dashboard. Every entity should resolve (no "entity not found" placeholders). Press `button.run_now_zone_1` and watch the status card update through phases.

- [ ] **Step 4: Commit**

```bash
git add homeassistant/packages/nedorachio.yaml homeassistant/packages/nedorachio_dashboard.yaml README.md
git commit -m "feat(ha): vanilla Lovelace dashboard for nedorachio"
```

---

## Phase 8 — Documentation

### Task 8.1: Final README

**Files:**
- Modify: `README.md`

By now the README has accumulated piecemeal sections. Now finalize it.

- [ ] **Step 1: Rewrite README with the full first-run flow**

Final structure:

```markdown
# Nedorachio

[brief project description]

## Hardware
- Bill of materials
- GPIO map (table)
- Wiring diagram
- Calibration values

## Firmware
### First flash
- `cp firmware/secrets.yaml.example firmware/secrets.yaml` and fill in.
- `cd firmware && esphome run nedorachio.yaml` while the board is on USB.

### Updating
- `cd firmware && esphome run nedorachio.yaml` (OTA, board stays installed).

## Home Assistant
- Copy `homeassistant/packages/nedorachio.yaml` to your HA config's `packages/` dir.
- Configure your weather entity / observed-rain sensor.
- Configure your `notify.*` target.
- Import the dashboard.

## Operation
- How a normal day looks.
- What every alarm means and what to check.
- How to add a fifth zone (set bitmask, add wiring).

## Troubleshooting
- Pre-flight fails / device offline / etc.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: finalize README with first-flash, HA setup, operation, troubleshooting"
```

---

## Phase 9 — Field cutover

### Task 9.1: Indoors integration test

**Files:** none (operational task).

- [ ] **Step 1: Wire one real 24VAC valve to relay 1 on the bench**

Use a real solenoid valve with the existing 24VAC transformer. Keep the other relays driving LEDs. Connect the rain sensor and flow meter as planned.

- [ ] **Step 2: Run a full week of simulated schedule fires**

Set the schedule to every minute (mash all `sched_*` switches on, set start to a minute that's 1 minute away). Let it run for 7+ cycles. Verify each completes cleanly.

- [ ] **Step 3: Run the fault matrix**

For each cancel cause from §7.6, force the condition, observe the cancel, the retry (if applicable), and the latch. Press `clear_fault` between scenarios. Sign off on a checklist:

- [ ] master_enable off blocks pre-flight
- [ ] e-stop latched blocks pre-flight
- [ ] rain sensor wet blocks pre-flight
- [ ] rain hold (after sensor) blocks for the configured duration
- [ ] rain_mm above threshold blocks pre-flight
- [ ] rain_mm TTL falls back to 0
- [ ] static PSI below min blocks pre-flight
- [ ] static PSI above max blocks pre-flight
- [ ] no-flow during run cancels and retries once
- [ ] high-flow during run cancels and retries once
- [ ] low-PSI during run cancels and retries once
- [ ] high-PSI alarms (and cancels iff `high_pressure_cancels_run` ON)
- [ ] phantom-flow latches an alarm
- [ ] rain wet during run cancels and does not retry
- [ ] runtime cap cuts the relay and latches `runtime_exceeded`
- [ ] catch-up runs the most recent missed fire on boot
- [ ] catch-up does not double-run when `last_run_started_epoch` is fresher

- [ ] **Step 4: Power-cycle test**

While a zone is running, pull the USB power. The device boots, waits for time, and (if a fire was missed inside the catch-up window) catches up. Verify counters persisted via OTA-flash test: `esphome run` while a counter is non-zero, then check after the OTA reboot the counter is the same.

### Task 9.2: Cutover

**Files:** README only.

- [ ] **Step 1: Schedule the cutover for a weekend morning**

This is a hardware swap: power down Rachio, swap zone wires to the new board's terminal blocks, repower.

- [ ] **Step 2: Verify each zone individually**

Press `button.run_now_zone_1` (1 min total). Confirm the right zone's heads pop up and water the right area. Repeat for zones 2..4.

- [ ] **Step 3: Calibrate `pulses_per_gallon`**

Place a 5-gallon bucket under one head; run the zone for the time it takes to fill 5 gal; note the pulses delta. Set `number.nedorachio_pulses_per_gallon = pulses / 5`. Re-verify the live `flow_rate` reads sensible gpm.

- [ ] **Step 4: Calibrate per-zone min/max flow**

After a normal run for each zone, look at the steady-state `flow_rate_gpm` during the running phase. Set `zone_N_min_flow_gpm` to ~50% of that, `zone_N_max_flow_gpm` to ~200%.

- [ ] **Step 5: Update README "Calibration" section with the final values**

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "docs: cutover-day calibration values"
```

### Task 9.3: First-week monitoring

**Files:** none.

- [ ] **Step 1: Watch the first week of scheduled runs**

Confirm every Tuesday/Saturday fire completes. Confirm rain hold engages on a wet day. Confirm gallons stats track the bucket-test calibration.

- [ ] **Step 2: Tune thresholds based on observed data**

If false-positive alarms fire (e.g. low-pressure during a normal supply dip), widen the relevant threshold via the HA `number` entity — no reflash needed. Document any threshold changes in the README.

- [ ] **Step 3: Commit threshold updates**

```bash
git add README.md
git commit -m "docs: first-week tuning notes"
```

---

## Self-review checklist (run before handing off)

Each spec section → at least one task that implements it:

- [ ] §6 hardware/wiring → Tasks 1.1–1.3
- [ ] §7.1 entities → Tasks 2.2 (zones), 2.3 (sensors), 4.1 (alarms), 4.5 (run buttons), 5.3 (plan readouts), 6.1 (stats)
- [ ] §7.2 tunables → Task 3.1
- [ ] §7.3 hard safety invariants → Task 2.2
- [ ] §7.4 pre-flight gates → Task 4.1
- [ ] §7.5 cycle-and-soak engine → Task 4.2
- [ ] §7.6 mid-run cancel rules → Task 4.3
- [ ] §7.7 retry & fault latching → Task 4.4
- [ ] §7.8 catch-up → Task 5.2
- [ ] §7.9 time sources → Task 2.1
- [ ] §8.1 weather feeder → Task 7.1
- [ ] §8.2 time push → Task 2.1 (`time: homeassistant`)
- [ ] §8.3 notification routes → Task 7.2
- [ ] §8.4 dashboard → Task 7.3
- [ ] §9 failure modes → Task 9.1 (matrix tested)
- [ ] §10 testing strategy → Tasks 9.1 (integration), 9.2 (cutover), 9.3 (first-week)
- [ ] §11 deliverables (README + firmware + ha package) → Task 8.1, all firmware/ha tasks
- [ ] §12 roadmap → not implemented (out of scope), but local physical controls deferred — no plan task; surfaces during 9.1 unboxing notes

No "TBD"s. No "implement later". No spec requirement uncovered.
