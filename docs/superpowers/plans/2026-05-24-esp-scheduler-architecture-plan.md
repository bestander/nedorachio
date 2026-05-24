# ESP Scheduler Architecture — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace scattered ESPHome lambdas and HA schedule duplication with one Python-canonical watering state machine, thin HA integration, and a C++ ESPHome component that executes the same plan→relay contract on device.

**Architecture:** `src/nedorachio/` owns `WateringSchedule` planning and `ScheduleEngine` execution. Relays are the only actuator output. HA pushes JSON config and reads plan/status sensors. Firmware YAML becomes hardware + component wiring.

**Tech Stack:** Python 3.11+, pytest, ESPHome external component (C++), Home Assistant package YAML.

**Spec:** `docs/superpowers/specs/2026-05-24-esp-scheduler-architecture-design.md`

---

## File structure

```
nedorachio/
├── src/nedorachio/
│   ├── __init__.py
│   ├── config.py           # parse nedorachio_config.yaml JSON schema
│   ├── models.py           # ZonePlan, WateringSchedule, RelayCommand, EngineState
│   ├── schedule.py         # cadence + window + rain + serialization
│   ├── gates.py            # preflight + mid-run cancel checks
│   ├── engine.py           # state machine tick loop
│   ├── actuator.py         # one-zone-max + inter-zone delay
│   └── tick.py             # wire inputs → plan → engine → relay commands
├── tests/
│   ├── unit/test_schedule.py
│   ├── unit/test_gates.py
│   ├── scenarios/test_engine_e2e.py   # migrated harness tests
│   └── golden/traces/                 # JSON golden files
├── firmware/
│   ├── nedorachio.yaml
│   ├── packages/hardware.yaml
│   ├── packages/exposure.yaml
│   └── components/nedorachio/         # Phase 3
└── homeassistant/packages/
    ├── nedorachio_config.yaml         # unchanged
    ├── nedorachio.yaml                # replaced (thin)
    └── nedorachio_dashboard.yaml      # entity ID updates only
```

---

## Phase 1 — Canonical Python library

### Task 1: Package skeleton and config loader

**Files:**
- Create: `src/nedorachio/__init__.py`
- Create: `src/nedorachio/models.py`
- Create: `src/nedorachio/config.py`
- Create: `tests/unit/test_config.py`
- Modify: `pyproject.toml` (add `packages = [{include = "nedorachio", from = "src"}]`)

- [ ] **Step 1: Write failing config test**

```python
# tests/unit/test_config.py
import json
from pathlib import Path

from nedorachio.config import load_profile


def test_load_profile_from_repo_config():
    raw = Path("homeassistant/packages/nedorachio_config.yaml").read_text()
    # profile lives in template attributes in HA yaml — extract JSON for test fixture
    start = raw.index('"{')
    end = raw.rindex('}"') + 2
    profile_json = json.loads(json.loads(raw[start:end]))
    profile = load_profile(profile_json)
    assert profile.global_.watering_window.timezone == "America/New_York"
    assert profile.zones[1].enabled is True
    assert profile.zones[1].goal_gallons_per_cycle == 400
    assert profile.zones[5].enabled is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_config.py -v`  
Expected: FAIL (`ModuleNotFoundError: nedorachio`)

- [ ] **Step 3: Implement minimal config loader**

```python
# src/nedorachio/models.py
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class WateringWindow:
    start: str
    end: str
    timezone: str


@dataclass(frozen=True)
class GlobalConfig:
    watering_window: WateringWindow
    rain_accumulation_threshold_mm_48h: float
    rain_accumulation_hold_hours_after_threshold: float
    attempt_cooldown_minutes: float
    maximum_runtime_minutes: float
    no_flow_grace_seconds: float
    no_flow_sustain_seconds: float
    blackout_weekdays: tuple[str, ...]


@dataclass(frozen=True)
class ZoneConfig:
    zone_id: int
    enabled: bool
    mode: str
    goal_gallons_per_cycle: float
    cycle_gallons: float
    soak_minutes: float
    minimum_interval_hours: float
    start_minimum_psi: float
    start_maximum_psi: float
    minimum_running_psi: float
    minimum_running_psi_grace_seconds: float
    minimum_flow_gpm: float
    maximum_flow_gpm: float


@dataclass(frozen=True)
class ConfigProfile:
    version: int
    global_: GlobalConfig
    zones: dict[int, ZoneConfig]
```

```python
# src/nedorachio/config.py
from __future__ import annotations
from nedorachio.models import ConfigProfile, GlobalConfig, WateringWindow, ZoneConfig

WEEKDAY_ALIASES = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}


def load_profile(data: dict) -> ConfigProfile:
    g = data["global"]
    ww = g["watering_window"]
    blackout = tuple(d.lower() for d in g.get("blackout", {}).get("weekdays", []))
    for d in blackout:
        if d not in WEEKDAY_ALIASES:
            raise ValueError(f"invalid blackout weekday: {d}")
    global_cfg = GlobalConfig(
        watering_window=WateringWindow(
            start=ww["start"],
            end=ww["end"],
            timezone=ww["timezone"],
        ),
        rain_accumulation_threshold_mm_48h=float(g["rain_accumulation_threshold_mm_48h"]),
        rain_accumulation_hold_hours_after_threshold=float(
            g["rain_accumulation_hold_hours_after_threshold"]
        ),
        attempt_cooldown_minutes=float(g["attempt_cooldown_minutes"]),
        maximum_runtime_minutes=float(g["maximum_runtime_minutes"]),
        no_flow_grace_seconds=float(g["no_flow_grace_seconds"]),
        no_flow_sustain_seconds=float(g["no_flow_sustain_seconds"]),
        blackout_weekdays=blackout,
    )
    zones: dict[int, ZoneConfig] = {}
    for key, z in data["zones"].items():
        zone_id = int(key)
        zones[zone_id] = ZoneConfig(
            zone_id=zone_id,
            enabled=bool(z["enabled"]),
            mode=str(z.get("mode", "gallons_target")),
            goal_gallons_per_cycle=float(z["goal_gallons_per_cycle"]),
            cycle_gallons=float(z["cycle_gallons"]),
            soak_minutes=float(z["soak_minutes"]),
            minimum_interval_hours=float(z["minimum_interval_hours"]),
            start_minimum_psi=float(z["start_minimum_psi"]),
            start_maximum_psi=float(z["start_maximum_psi"]),
            minimum_running_psi=float(z["minimum_running_psi"]),
            minimum_running_psi_grace_seconds=float(z["minimum_running_psi_grace_seconds"]),
            minimum_flow_gpm=float(z["minimum_flow_gpm"]),
            maximum_flow_gpm=float(z["maximum_flow_gpm"]),
        )
    return ConfigProfile(version=int(data["version"]), global_=global_cfg, zones=zones)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_config.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/nedorachio pyproject.toml tests/unit/test_config.py
git commit -m "feat: add canonical config profile loader"
```

---

### Task 2: Schedule planner (next watering)

**Files:**
- Create: `src/nedorachio/schedule.py`
- Create: `tests/unit/test_schedule.py`

- [ ] **Step 1: Write failing tests for cadence + window + blackout**

```python
# tests/unit/test_schedule.py
from datetime import datetime
from zoneinfo import ZoneInfo

from nedorachio.config import load_profile
from nedorachio.models import ZoneRuntime
from nedorachio.schedule import compute_watering_schedule


FIXTURE = {
    "version": 1,
    "global": {
        "watering_window": {"start": "23:00", "end": "09:00", "timezone": "America/New_York"},
        "rain_accumulation_threshold_mm_48h": 5,
        "rain_accumulation_hold_hours_after_threshold": 24,
        "attempt_cooldown_minutes": 20,
        "maximum_runtime_minutes": 60,
        "no_flow_grace_seconds": 60,
        "no_flow_sustain_seconds": 30,
        "blackout": {"weekdays": ["thu", "fri"]},
    },
    "zones": {
        "1": {
            "enabled": True,
            "mode": "gallons_target",
            "goal_gallons_per_cycle": 400,
            "cycle_gallons": 200,
            "soak_minutes": 15,
            "minimum_interval_hours": 72,
            "start_minimum_psi": 35,
            "start_maximum_psi": 85,
            "minimum_running_psi": 20,
            "minimum_running_psi_grace_seconds": 60,
            "minimum_flow_gpm": 0.2,
            "maximum_flow_gpm": 12.0,
        }
    },
}


def _epoch(y, m, d, h, mi=0):
    return int(datetime(y, m, d, h, mi, tzinfo=ZoneInfo("America/New_York")).timestamp())


def test_zone_not_due_before_interval():
    profile = load_profile(FIXTURE)
    runtime = {1: ZoneRuntime(last_finished_epoch=_epoch(2026, 6, 1, 6, 0))}
    plan = compute_watering_schedule(profile, now_epoch=_epoch(2026, 6, 2, 6, 0), runtime=runtime)
    z1 = plan.zones[1]
    assert z1.blocked_reason == "not_due"


def test_due_zone_snaps_into_overnight_window():
    profile = load_profile(FIXTURE)
    runtime = {1: ZoneRuntime(last_finished_epoch=_epoch(2026, 5, 28, 6, 0))}
    plan = compute_watering_schedule(profile, now_epoch=_epoch(2026, 6, 1, 10, 0), runtime=runtime)
    z1 = plan.zones[1]
    assert z1.next_start_epoch is not None
    start_local = datetime.fromtimestamp(z1.next_start_epoch, ZoneInfo("America/New_York"))
    assert start_local.hour >= 23 or start_local.hour < 9


def test_blackout_pushes_off_blackout_day():
    profile = load_profile(FIXTURE)
    runtime = {1: ZoneRuntime(last_finished_epoch=_epoch(2026, 5, 26, 6, 0))}
    # 2026-06-04 is Thursday
    plan = compute_watering_schedule(profile, now_epoch=_epoch(2026, 6, 4, 6, 0), runtime=runtime)
    start_local = datetime.fromtimestamp(plan.zones[1].next_start_epoch, ZoneInfo("America/New_York"))
    assert start_local.strftime("%a").lower() not in ("thu", "fri")
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `pytest tests/unit/test_schedule.py -v`

- [ ] **Step 3: Implement `compute_watering_schedule`**

Implement in `src/nedorachio/schedule.py`:

- `ZoneRuntime(last_finished_epoch, cycle_delivered_gallons=0)`
- `WateringSchedule` with `zones: dict[int, ZonePlan]`
- Cadence: `last_finished + min_interval_hours`
- Window snap: if local time outside `[start, end)` (overnight-aware), move to next window open
- Blackout: skip Thu/Fri local dates
- Rain hold hooks (stub `rain_blocked=False` until Task 4)
- Serialization: sort due zones by `next_start_epoch`, add `maximum_runtime_minutes` spacing for queue order

- [ ] **Step 4: Run tests — expect PASS**

Run: `pytest tests/unit/test_schedule.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/nedorachio/schedule.py src/nedorachio/models.py tests/unit/test_schedule.py
git commit -m "feat: compute next watering schedule from config and runtime"
```

---

### Task 3: Actuator interlocks

**Files:**
- Create: `src/nedorachio/actuator.py`
- Create: `tests/unit/test_actuator.py`

- [ ] **Step 1: Write failing interlock tests**

```python
# tests/unit/test_actuator.py
from nedorachio.actuator import RelayActuator, RelayCommand


def test_only_one_zone_on():
    act = RelayActuator(inter_zone_delay_s=2)
    act.apply(RelayCommand(zone_id=1, desired_on=True, reason="test"))
    assert act.current_zone == 1
    act.apply(RelayCommand(zone_id=2, desired_on=True, reason="test"))
    assert act.current_zone == 2
    assert act.history[-2].zone_id == 1
    assert act.history[-2].desired_on is False


def test_emergency_stop_clears_all():
    act = RelayActuator(inter_zone_delay_s=0)
    act.apply(RelayCommand(zone_id=3, desired_on=True, reason="test"))
    act.emergency_stop()
    assert act.current_zone == 0
```

- [ ] **Step 2–4: Implement, run, pass**

- [ ] **Step 5: Commit**

---

### Task 4: Preflight gates and rain hold

**Files:**
- Create: `src/nedorachio/gates.py`
- Modify: `src/nedorachio/schedule.py` (rain hold in planner)
- Create: `tests/unit/test_gates.py`

- [ ] **Step 1: Write failing gate tests**

Cover at minimum:

- `start_minimum_psi` / `start_maximum_psi` preflight
- rain sensor wet hold
- `rain_mm_last_48h` over threshold hold
- phantom flow latched blocks start

Port conditions from `tests/controller/test_scenarios.py` — pick 3 representative cases first.

- [ ] **Step 2–4: Implement `evaluate_preflight(...)` and rain hold in planner**

- [ ] **Step 5: Commit**

---

### Task 5: ScheduleEngine state machine

**Files:**
- Create: `src/nedorachio/engine.py`
- Create: `src/nedorachio/tick.py`
- Create: `tests/scenarios/test_engine_e2e.py`

- [ ] **Step 1: Write one failing E2E scenario (gallons target happy path)**

```python
# tests/scenarios/test_engine_e2e.py
from nedorachio.tick import Controller


def test_gallons_target_completes_one_cycle():
    ctl = Controller.from_repo_fixture(fast=True)
    ctl.set_time(2026, 6, 3, 6, 0)
    ctl.make_zone_due(1)
    ctl.advance_until_idle(max_seconds=3600)
    assert ctl.last_run_outcome == "completed"
    assert ctl.relay_history[-1].desired_on is False
    assert ctl.schedule.zones[1].cycle_delivered_gallons == 0  # reset after complete
```

- [ ] **Step 2–4: Implement `Controller` wrapping engine tick loop**

`tick.py` each second:

1. Read sensor snapshot
2. `compute_watering_schedule`
3. `engine.advance(schedule, sensors)`
4. Apply returned `RelayCommand`s through `RelayActuator`
5. Append to `relay_history` with plan snapshot hash for trace tests

States: `IDLE`, `PREFLIGHT`, `RUNNING`, `SOAKING`, `INTER_ZONE`, `FAULT`

- [ ] **Step 5: Migrate remaining scenarios from `tests/controller/test_scenarios.py`**

Run: `pytest tests/scenarios/ -v`

Target: all existing scenario names pass against new library.

- [ ] **Step 6: Commit**

```bash
git commit -m "feat: schedule engine state machine with relay trace history"
```

---

### Task 6: Golden trace harness

**Files:**
- Create: `tests/golden/trace_runner.py`
- Create: `tests/golden/traces/zone1_happy_path.json`
- Create: `tests/golden/test_traces.py`

- [ ] **Step 1: Define trace JSON schema**

```json
{
  "name": "zone1_happy_path",
  "config": "repo_fixture",
  "steps": [
    {"at": "2026-06-03T06:00:00-04:00", "action": "make_zone_due", "zone": 1},
    {"at": "2026-06-03T06:00:01-04:00", "action": "advance_seconds", "seconds": 3600}
  ],
  "expect": {
    "relay_events": [
      {"zone_id": 1, "desired_on": true, "reason": "schedule_start"},
      {"zone_id": 1, "desired_on": false, "reason": "chunk_complete"}
    ],
    "last_outcome": "completed"
  }
}
```

- [ ] **Step 2: Implement trace runner + one golden file**

- [ ] **Step 3: Run `pytest tests/golden/ -v`**

- [ ] **Step 4: Commit**

---

## Phase 2 — Thin Home Assistant

### Task 7: HA JSON persistence + thin package

**Files:**
- Create: `homeassistant/packages/nedorachio_state.yaml`
- Create: `homeassistant/nedorachio/.gitkeep`
- Modify: `.gitignore` (add `homeassistant/nedorachio/runtime_state.json`)
- Modify: `homeassistant/packages/nedorachio.yaml` (replace body)
- Modify: `homeassistant/packages/nedorachio_dashboard.yaml` (entity IDs)
- Create: `src/nedorachio/runtime_state.py`
- Create: `tests/unit/test_runtime_state.py`

Do **not** commit `runtime_state.json` — it is created on the HA host at first start.

- [ ] **Step 1: Runtime state model + round-trip test**

```python
# tests/unit/test_runtime_state.py
from nedorachio.runtime_state import RuntimeState, cold_start_runtime_state


def test_runtime_state_round_trip_json():
    state = cold_start_runtime_state(now_epoch=1717412400)
    state.zones[1].last_finished_epoch = 1717000000
    state.zones[1].cycle_delivered_gallons = 120.5
    blob = state.to_json()
    restored = RuntimeState.from_json(blob)
    assert restored.zones[1].last_finished_epoch == 1717000000
    assert restored.updated_epoch == 1717412400


def test_cold_start_has_zero_cadence():
    state = cold_start_runtime_state(now_epoch=1000)
    for z in range(1, 9):
        assert state.zones[z].last_finished_epoch == 0
        assert state.zones[z].cycle_delivered_gallons == 0.0
    assert state.updated_epoch == 1000
```

```python
# src/nedorachio/runtime_state.py (minimal)
def cold_start_runtime_state(*, now_epoch: int) -> RuntimeState:
    """Empty cadence — used when runtime_state.json is missing on disk."""
    ...
```

- [ ] **Step 2: Create `nedorachio_state.yaml` cold-start seed**

Template sensor with `state` attribute containing the same JSON as `cold_start_runtime_state()` (version, zeroed zones). This is the repo-owned default; the live file is gitignored.

- [ ] **Step 3: Add `.gitignore` entry and `ensure_runtime_state` script**

```gitignore
homeassistant/nedorachio/runtime_state.json
```

```yaml
shell_command:
  nedorachio_write_runtime_state: >-
    python3 -c "import json,sys; open('{{ config_dir }}/nedorachio/runtime_state.json','w').write(sys.argv[1])"
    "{{ payload }}"

script:
  nedorachio_ensure_runtime_state:
    alias: "Nedorachio: create runtime_state.json on cold start"
    mode: single
    sequence:
      - variables:
          path: "{{ config_dir }}/nedorachio/runtime_state.json"
          seed: "{{ state_attr('sensor.nedorachio_runtime_state_seed', 'state') | default('{}', true) }}"
      - service: shell_command.nedorachio_write_runtime_state
        data:
          payload: "{{ seed }}"
        continue_on_error: true
      # Implementation: use a small ensure script that skips write if file exists
      # and is valid JSON; only writes seed when missing/empty/invalid.
```

Add `automation` on `homeassistant` start → `script.nedorachio_ensure_runtime_state` **before** push/pull sync.

- [ ] **Step 4: New minimal `nedorachio.yaml` sync scripts**

Keep:

1. `automation`: push `rain_mm_last_48h` every 10 min
2. `script.nedorachio_apply_config`: profile JSON → device `text.config_profile`
3. `script.nedorachio_ensure_runtime_state`: create file from seed if missing/empty (Step 3)
4. `script.nedorachio_push_runtime_state`: read `runtime_state.json` → device `text.runtime_state` (when device idle)
5. `script.nedorachio_pull_runtime_state`: device `text.runtime_state` publish → write `runtime_state.json` (compare `updated_epoch`)
6. `automation`: on HA start → ensure, then on device reconnect + idle → push
7. `automation`: on device runtime_state change → pull
8. Alarm notify automations

Delete:

- `nedorachio_reconcile_cadence` and all cadence repair scripts
- All per-zone `number.set_value` loops (config and persistence)
- Template sensors for `next_run_local`
- Aggregator template for per-field persisted state

Example pull script skeleton:

```yaml
script:
  nedorachio_pull_runtime_state:
    mode: queued
    sequence:
      - variables:
          device_json: "{{ states('text.nedorachio_runtime_state') }}"
          device_epoch: "{{ (device_json | from_json(default={})).get('updated_epoch', 0) | int(0) }}"
          file_epoch: "{{ state_attr('sensor.nedorachio_runtime_state_file', 'updated_epoch') | int(0) }}"
      - condition: template
        value_template: "{{ device_epoch >= file_epoch }}"
      - service: shell_command.nedorachio_write_runtime_state
        data:
          payload: "{{ device_json }}"
```

(`shell_command` writes `homeassistant/nedorachio/runtime_state.json`; add matching read helper for push.)

- [ ] **Step 5: Wire engine to emit runtime JSON on state change**

In `src/nedorachio/engine.py`, bump `updated_epoch` and expose `runtime_state()` after every mutation that affects cadence or partial cycles.

- [ ] **Step 6: Update dashboard entity IDs**

Map to component naming, e.g.:

- `sensor.nedorachio_zone_1_next_start` (device-computed)
- Remove HA-only persisted state cards if any

- [ ] **Step 7: Manual review checklist**

- [ ] Config profile JSON matches `load_profile` schema
- [ ] Dashboard loads with no `unknown entity` for enabled zones 1–4
- [ ] No Jinja schedule math remains in HA package

- [ ] Cold start: delete `runtime_state.json`, restart HA → file recreated from seed, zones due per clean cadence
- [ ] **Step 8: Commit**

```bash
git commit -m "refactor: HA JSON persistence with gitignored runtime_state cold start"
```

---

## Phase 3 — C++ ESPHome component

### Task 8: Component scaffold

**Files:**
- Create: `firmware/components/nedorachio/__init__.py` (ESPHome package)
- Create: `firmware/components/nedorachio/nedorachio.h`
- Create: `firmware/components/nedorachio/nedorachio.cpp`
- Create: `firmware/components/nedorachio/controller.h` (port interfaces)
- Modify: `firmware/nedorachio.yaml`

- [ ] **Step 1: Register external component**

```yaml
# firmware/nedorachio.yaml (excerpt)
external_components:
  - source:
      type: local
      path: components

nedorachio:
  id: irrigation
  timezone: America/New_York
```

- [ ] **Step 2: Component owns relay outputs internally**

Zone switches become read-only HA reflections of actuator state (or internal-only with manual run API on component).

- [ ] **Step 3: Validate YAML compiles**

Run: `esphome config firmware/nedorachio.yaml`

- [ ] **Step 4: Commit scaffold**

---

### Task 9: Port schedule planner to C++

**Files:**
- Create: `firmware/components/nedorachio/schedule.cpp`
- Create: `firmware/components/nedorachio/schedule.h`

- [ ] **Step 1: Port unit-level functions matching Python tests**

Translate `compute_watering_schedule` with identical outputs for golden trace inputs.

- [ ] **Step 2: Host-side test runner (optional)**

Run golden JSON through Python reference and C++ test binary; assert matching `next_start_epoch` per zone.

- [ ] **Step 3: Commit**

---

### Task 10: Port engine + wire sensors

**Files:**
- Modify: `firmware/components/nedorachio/nedorachio.cpp`
- Create: `firmware/packages/hardware.yaml` (relays, ADC, pulse counter, rain)
- Create: `firmware/packages/exposure.yaml` (HA sensors)

- [ ] **Step 1: 1 Hz `interval` tick calling engine**

No lambdas >5 lines in YAML.

- [ ] **Step 2: Expose plan sensors from C++**

`sensor.zone_N_next_start`, `sensor.current_phase`, etc.

- [ ] **Step 3: Bench test**

Flash bench harness; confirm zone 1 manual run and scheduled run match golden trace ordering.

- [ ] **Step 4: Commit**

---

## Phase 4 — Decommission legacy

### Task 11: Remove old firmware logic packages

**Files:**
- Delete: `firmware/packages/05-engine.yaml`
- Delete: `firmware/packages/06-schedule.yaml`
- Delete: `firmware/packages/07-stats.yaml`
- Delete: `firmware/packages/09-ha-state.yaml`
- Delete or shrink: `firmware/packages/04-tunables.yaml` (only hardware constants remain)
- Delete: `tests/controller/simulator.py`
- Delete: `tests/controller/firmware_contract.py`
- Modify: `tests/controller/harness.py` → import from `nedorachio.tick.Controller`

- [ ] **Step 1: Point harness at canonical library**

- [ ] **Step 2: Run full test suite**

Run: `pytest -v`  
Expected: all pass

- [ ] **Step 3: Validate firmware**

Run: `esphome config firmware/nedorachio.yaml`

- [ ] **Step 4: Commit**

```bash
git commit -m "chore: remove legacy lambda engine and simulator mirror"
```

---

## Verification checklist (before calling v2 done)

- [ ] `pytest` completes in &lt;10s on CI hardware
- [ ] At least 5 golden traces cover: happy path, rain hold, blackout, preflight PSI fail, mid-run no-flow cancel
- [ ] `homeassistant/packages/nedorachio.yaml` &lt; 150 lines
- [ ] `grep -c lambda: firmware/packages/*.yaml` → 0 in deleted packages; only hardware calibration lambdas remain (if any)
- [ ] Dashboard next-watering cards use device sensors only
- [ ] README updated with new architecture diagram and dev workflow (`pytest` first, flash second)

---

## Execution handoff

Plan complete. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks  
2. **Inline Execution** — execute tasks in one session with checkpoints after Phase 1 and Phase 3

Which approach do you want to use?
