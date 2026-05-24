# Nedorachio — ESP Scheduler Architecture (v2)

**Date:** 2026-05-24  
**Status:** Draft for review  
**Supersedes:** Partially supersedes `2026-04-30-nedorachio-irrigation-controller-design.md` and `2026-05-11-ha-exposure-and-config-design.md` on *implementation shape*. Hardware, safety goals, and HA-minimal exposure principles are retained.

---

## 1. Problem

The current implementation spreads irrigation logic across three incompatible surfaces:

| Surface | What it does today | Why it fails |
|---------|-------------------|--------------|
| **Firmware YAML** (`firmware/packages/05-engine.yaml`, `06-schedule.yaml`, …) | ~250 C++ `lambda:` blocks and `script:` steps implement scheduling, pre-flight, cycle-and-soak, plan readout | Hard to read, hard to test on host, ESPHome anti-patterns (blocking `delay()` in lambdas) require separate contract tests |
| **Home Assistant** (`homeassistant/packages/nedorachio.yaml`, 1179 lines) | Pushes 80+ `number.set_value` calls, reconciles cadence, restores persisted state, computes `next_run` template sensors | Duplicates device logic; drift between HA plan and device plan; fragile Jinja |
| **Python simulator** (`tests/controller/simulator.py`) | Re-implements firmware semantics for E2E tests | Third copy of the algorithm; catches drift only after bugs ship |

There is no single artifact that answers: *“Given config + time + sensors, when will each zone run, and when will relays actually fire?”*

---

## 2. Goals

1. **One canonical algorithm** — a real program (not YAML lambdas) that owns the full watering state machine.
2. **Schedule as the core domain object** — “next watering schedule” is computed, exposed, and kept in lockstep with relay outcomes.
3. **Relays as the output boundary** — zone switches are the only hardware actuators; the algorithm decides when they turn on/off internally.
4. **Config from `nedorachio_config.yaml`** — HA JSON profile is the operator-facing config contract; no per-field entity sprawl.
5. **Thin Home Assistant** — weather feeder, config push, notifications, dashboard. No schedule math in HA.
6. **Testable without hardware** — exhaustive scenario tests against the canonical library; firmware is a thin runtime wrapper.

## 3. Non-goals

- Rewriting hardware wiring or GPIO map.
- ET-based or Smart Irrigation integration (roadmap item).
- Running Python on the ESP32 (not supported by ESPHome).
- HA-owned scheduling or cadence reconciliation.
- Exposing every tunable as an HA entity.

---

## 4. Recommended approach

### 4.1 Three options considered

| Option | Description | Pros | Cons |
|--------|-------------|------|------|
| **A. Python canonical + C++ ESPHome component** *(recommended)* | State machine in `src/nedorachio/` (Python). C++ external component on device runs a port of the same logic. ESPHome YAML = hardware only. | Fast iteration & exhaustive tests in Python; real code on device; single mental model | Requires disciplined 1:1 port; two languages |
| **B. C++ only in ESPHome external component** | All logic in C++; host gtest unit tests | Single runtime language | Slower dev loop; harder scenario coverage; no reuse of existing simulator investment |
| **C. Refactor YAML lambdas into scripts only** | Keep ESPHome-native style, reduce lambda size | Smallest migration | Still untestable on host; does not fix HA duplication |

**Recommendation: Option A.** The existing Python simulator and test harness are the right *shape*; they should become the product, not a mirror. The C++ component is a runtime adapter, not a second design.

### 4.2 High-level architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Home Assistant (thin)                        │
│  nedorachio_config.yaml ──► push JSON profile                  │
│  weather entity ──────────► rain_mm_last_48h                     │
│  dashboard / notifications ◄── status & plan sensors             │
└───────────────────────────────┬─────────────────────────────────┘
                                │ ESPHome native API
┌───────────────────────────────┴─────────────────────────────────┐
│              ESPHome device (hardware shell)                     │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────────────┐ │
│  │ GPIO relays │  │ Sensors      │  │ nedorachio component    │ │
│  │ (zones 1-8) │◄─┤ rain, flow,  │──►│ ScheduleEngine (C++ port)│ │
│  │             │  │ pressure, time│  │  • plan                  │ │
│  └─────────────┘  └──────────────┘  │  • preflight + run       │ │
│                                      │  • relay commands        │ │
│                                      └─────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘

        ┌──────────────────────────────────────┐
        │  src/nedorachio/ (Python, canonical) │
        │  • same state machine                  │
        │  • pytest scenario suite               │
        │  • golden traces for C++ parity        │
        └──────────────────────────────────────┘
```

---

## 5. Domain model

### 5.1 Config profile (input)

Source: `homeassistant/packages/nedorachio_config.yaml` — unchanged JSON schema (`version`, `global`, `zones`).

Parsed once into a typed `ConfigProfile`:

- **Global:** watering window, timezone, rain thresholds, cooldown, runtime cap, flow/pressure gate timings, blackout weekdays.
- **Per zone:** enabled, mode (`gallons_target` | `minutes`), goal/cycle gallons or minutes, soak, min interval, PSI/GPM limits.

The device receives the profile as a single JSON blob (text entity or custom API action), not 80 individual `number` writes.

### 5.2 Inputs (each tick)

| Input | Source |
|-------|--------|
| `now_epoch` | NTP / HA time |
| `config` | Last applied profile |
| `pressure_psi` | ADC |
| `flow_gpm`, `flow_pulses_total` | Pulse meter |
| `rain_sensor_wet` | Binary input |
| `rain_mm_last_48h` | HA push (optional; stale → ignore) |
| `master_schedule_enabled` | HA switch |
| `manual_zone_request` | HA zone switch edge (optional override) |

### 5.3 WateringSchedule (core output)

The schedule is a first-class structure, not a side effect of relay timing:

```python
@dataclass
class ZonePlan:
    zone_id: int
    next_start_epoch: int | None      # None if disabled or blocked indefinitely
    blocked_reason: str | None        # e.g. "blackout", "rain_hold", "not_due"
    cycle_delivered_gallons: float
    cycle_remaining_gallons: float
    last_finished_epoch: int

@dataclass
class WateringSchedule:
    computed_at_epoch: int
    zones: list[ZonePlan]             # one per enabled zone
    next_action_epoch: int | None     # earliest relay event (start, soak end, …)
    next_action: str                  # "start_zone_3", "idle", …
```

**Invariant:** Every relay transition must correspond to an executed `ScheduleAction` that was already reflected in `next_action` / `next_action_epoch` at the prior plan snapshot (or be a documented manual/emergency override).

### 5.4 Relay outcomes (output)

```python
@dataclass
class RelayCommand:
    zone_id: int          # 0 = all off
    desired_on: bool
    reason: str           # "schedule_start", "soak_complete", "preflight_fail", …
```

Only the actuator layer applies `RelayCommand` to GPIO. It enforces:

- At most one zone ON.
- Inter-zone close→open delay.
- Emergency stop clears pending plan execution.

---

## 6. State machine

Single `ScheduleEngine` with explicit states:

```
                    ┌──────────┐
         ┌─────────►│   IDLE   │◄────────────────┐
         │          └────┬─────┘                 │
         │               │ due + gates pass      │
         │               ▼                       │
         │          ┌──────────┐                 │
         │          │ PREFLIGHT│──fail──► cooldown, replan
         │          └────┬─────┘                 │
         │               │ pass                  │
         │               ▼                       │
         │     ┌─────────────────────┐          │
         │     │ RUNNING (chunk)     │          │
         │     └─────────┬───────────┘          │
         │               │ goal/chunk met       │
         │               ▼                       │
         │     ┌─────────────────────┐          │
         │     │ SOAKING             │──────────┤
         │     └─────────┬───────────┘          │
         │               │ more chunks?          │
         │               ├──yes──► RUNNING       │
         │               │ no                    │
         │               ▼                       │
         │          complete ────────────────────┘
         │
    replan on: time tick, config change, sensor threshold,
               run complete/cancel, manual request
```

**Planning** runs on every tick *before* state transitions:

1. For each enabled zone, compute cadence due time (`last_finished + min_interval`).
2. Snap to watering window + skip blackout weekdays.
3. Apply rain holds (sensor + forecast).
4. Serialize zones (single-zone invariant): queue order by due time, spacing by `maximum_runtime_minutes`.
5. Publish `WateringSchedule`.

**Execution** consumes the head of the queue when `now >= next_action_epoch` and state is `IDLE`.

Existing behaviors preserved (from v1 spec):

- Gallons-target cycle-and-soak with mid-run flow/pressure cancels.
- Pre-flight gates (PSI, rain, latched phantom flow).
- Attempt cooldown after failed/skipped starts.
- Skip-next-run, master schedule disable, emergency stop.
- Stats counters on successful delivery.

---

## 7. ESPHome surface (device → HA)

### 7.1 Writable (operator)

| Entity | Purpose |
|--------|---------|
| `switch.zone_1` … `zone_8` | Manual run / stop (delegates to engine, not raw GPIO) |
| `switch.master_schedule_enabled` | Global schedule arm |
| `button.emergency_stop` | Immediate all-off |
| `button.skip_next_run` | Veto next scheduled start |
| `button.clear_fault` | Clear latched phantom-flow alarm |
| `text.config_profile` (or custom service) | HA pushes JSON profile |

### 7.2 Read-only (dashboard)

| Entity | Purpose |
|--------|---------|
| `sensor.pressure_psi`, `sensor.flow_rate_gpm`, `sensor.total_gallons` | Live telemetry |
| `binary_sensor.rain_sensor` | Wet/dry |
| `sensor.current_phase`, `sensor.running_zone`, `sensor.run_progress` | Run state |
| `sensor.zone_N_next_start` | From `WateringSchedule` (device-computed) |
| `sensor.zone_N_blocked_reason` | Why next start is null |
| `sensor.last_run_outcome`, `sensor.last_stop_reason` | Outcomes |

**Removed from default HA exposure:** per-zone tunable numbers, per-field epoch/gallon writers, cadence reconcile scripts, HA-computed next_run templates.

### 7.3 HA package split (target)

| File | Role |
|------|------|
| `nedorachio_config.yaml` | Operator config — static JSON profile (unchanged) |
| `nedorachio_runtime_state.json` | Runtime persistence JSON (cold-start zeros; updated by pull) |
| `nedorachio.yaml` | Sync automations: ensure/push/pull runtime, apply config, weather, alarms |
| `nedorachio_templates.yaml` | Dashboard display templates (device plan readouts only) |
| `nedorachio_dashboard.yaml` | Lovelace (update entity IDs only) |

---

## 7.4 Persistence — HA JSON (authoritative)

Runtime state lives in Home Assistant as JSON at `packages/nedorachio_runtime_state.json`, mirroring the config-profile pattern. The device holds a **working copy in RAM** for autonomous operation; HA holds the **durable copy** on disk for survive-flash and survive-HA-restart. The file is **committed in this repo** (cold-start zeros) and updated by pull automations as the device runs.

### Two JSON documents

| Document | Edited by | Purpose |
|----------|-----------|---------|
| **Profile** (`nedorachio_config.yaml`) | Operator in git | Zones, windows, thresholds — immutable during a run |
| **Runtime state** (`packages/nedorachio_runtime_state.json`) | Device → HA pull / HA → device push | Cadence backup, partial cycles, rain-hold epochs |

### Runtime state schema (v1)

```json
{
  "version": 1,
  "updated_epoch": 1717412400,
  "zones": {
    "1": {
      "last_finished_epoch": 1717000000,
      "cycle_delivered_gallons": 0.0
    }
  },
  "rain_sensor_last_wet_epoch": 0,
  "rain_forecast_last_high_epoch": 0,
  "last_non_completed_attempt_epoch": 0,
  "stats": {
    "zone_1_gallons_total": 12400.0,
    "zone_1_run_count_total": 31
  }
}
```

Only fields the planner/engine need across reboot are included. Ephemeral phase timing (`phase_seconds_left`, etc.) is not persisted.

### Storage mechanism

Template sensors alone do **not** survive HA restarts with updated values. Use:

1. **`packages/nedorachio_runtime_state.json`** — authoritative durable store in the HA packages directory (version-controlled in this repo). Created from cold-start seed on first HA start if missing or invalid.
2. **`homeassistant/scripts/nedorachio_runtime_sync.py`** — ensure/write helper invoked by HA `shell_command`.
3. **Device number entities** — bridge until Phase 3 `text.runtime_state` (pull reads all fields including `last_finished`; push writes only HA-writable fields: cycle gallons, rain epochs, cooldown epoch).

No per-field `number` entities. No cadence reconcile scripts. One blob in, one blob out.

### Cold start

On HA start (and before any push/pull sync), `script.nedorachio_ensure_runtime_state` runs:

```
if runtime_state.json missing, empty, or invalid JSON:
  write cold-start JSON via nedorachio_runtime_sync.py ensure
  (all zones: last_finished_epoch = 0, cycle_delivered_gallons = 0, updated_epoch = now)
else:
  use existing file as-is
```

Cold-start means every zone is treated as never watered — the planner applies normal cadence/window rules from a clean slate. After the first successful run, pull automations update the JSON file from device state.

Operators can reset cadence by deleting `runtime_state.json` and restarting HA (or calling an explicit reset script that rewrites the seed).

### Sync flow

```
HA start:
  ensure_runtime_state (create file from seed if needed)

Boot / reconnect (device idle):
  HA reads runtime_state.json → text.set_value on device → engine loads state

During operation (device autonomous):
  engine mutates RAM state → publishes JSON → HA writes runtime_state.json

Device flash / RAM loss:
  HA pushes runtime_state.json on connect → schedule continues correctly

HA restart:
  runtime_state.json on disk is unchanged → push to device when it reconnects
```

### Conflict rule

Compare `updated_epoch` in the JSON blob:

- **HA file newer** → HA wins on push (e.g. operator restored a backup copy).
- **Device publish newer** → device wins; HA overwrites file.
- **Equal / missing** → no push; device keeps working copy.

The engine on device always runs from RAM. HA JSON is not consulted every tick — only on load and save events. Irrigation continues when HA is down; state catches up when HA returns.

### What this replaces

The current approach (8× `last_finished` number entities + template aggregation + reconcile scripts + ignored HA writes in `09-ha-state.yaml`) collapses to **two JSON sync paths**: config profile and runtime state.

---

## 8. Testing strategy

### 8.1 Canonical tests (Python)

Replace `simulator.py` mirror with imports from `src/nedorachio/`:

- **Unit:** plan computation (blackout, rain hold, cadence, serialization).
- **Scenario:** full tick loops with mock sensors (existing harness API preserved).
- **Golden traces:** JSON files `{ inputs[], expected_plan_snapshots[], expected_relay_events[] }` checked into repo.

### 8.2 Firmware parity

- C++ component replays golden traces in host build (optional Phase 2) or on-device test mode.
- Until parity suite exists, **Python tests gate merges**; C++ port follows spec section-by-section.
- Retire `firmware_contract.py` lambda-pattern checks once lambdas are gone.

### 8.3 What we stop testing

- Static YAML regex contracts for lambda anti-patterns (deleted with lambdas).
- HA Jinja schedule math (deleted with template sensors).

---

## 9. Migration path

Four phases, each shippable:

| Phase | Deliverable | HA/firmware state |
|-------|-------------|-------------------|
| **1. Extract library** | `src/nedorachio/` + tests port from simulator; config loader from `nedorachio_config.yaml` schema | Old firmware still runs |
| **2. Thin HA** | New minimal `nedorachio.yaml`; dashboard entity ID updates | Old firmware; profile via JSON push prototype |
| **3. C++ component** | `firmware/components/nedorachio/`; YAML packages shrink to hardware + component | Device runs new engine |
| **4. Decommission** | Delete old engine/schedule/stats lambda packages; remove simulator mirror | Single code path |

---

## 10. File layout (target)

```
nedorachio/
├── src/nedorachio/
│   ├── __init__.py
│   ├── config.py              # ConfigProfile from JSON
│   ├── schedule.py            # WateringSchedule planner
│   ├── engine.py              # State machine
│   ├── actuator.py            # RelayCommand + interlocks
│   ├── sensors.py             # Input snapshot types
│   └── tick.py                # advance one second
├── tests/
│   ├── unit/                  # plan math
│   ├── scenarios/             # harness E2E (migrate from tests/controller/)
│   └── golden/                # trace files
├── firmware/
│   ├── nedorachio.yaml        # board, wifi, includes component
│   ├── packages/
│   │   ├── hardware.yaml      # relays, sensors, pins
│   │   └── exposure.yaml      # HA entity declarations for component
│   └── components/nedorachio/ # C++ external component
├── homeassistant/
│   ├── packages/
│   │   ├── nedorachio_config.yaml
│   │   ├── nedorachio_runtime_state.json
│   │   ├── nedorachio.yaml
│   │   ├── nedorachio_templates.yaml
│   │   └── nedorachio_dashboard.yaml
│   └── scripts/
│       └── nedorachio_runtime_sync.py
```

---

## 11. Open decisions (defaults chosen)

| Question | Default for v2 |
|----------|----------------|
| Persistence | **`packages/nedorachio_runtime_state.json`** — committed cold-start; HA pull updates from device; push restores writable fields on connect |
| Timezone | From config profile `global.watering_window.timezone`; planner uses zone-aware local time |
| Manual zone ON | Starts immediate single-zone run through engine (same preflight); does not bypass safety |
| Python/C++ drift | Golden traces required before Phase 3 merge |

---

## 12. Success criteria

1. A developer can read `engine.py` + `schedule.py` and understand the full watering flow without opening YAML.
2. `nedorachio.yaml` HA package is under 150 lines and contains zero schedule math.
3. Dashboard “Next watering” cards read device sensors only.
4. Scenario test suite runs in &lt;5s and covers all gate reasons in the state machine.
5. Every relay ON/OFF in tests appears in both `expected_relay_events` and plan snapshot before the event.
