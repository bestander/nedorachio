# HA Exposure + Gallons Target Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver minimal HA controls, bidirectional sensor visibility, YAML-driven overrides, and per-zone dual scheduling with gallons-target default behavior (including soak, PSI guards, and cooldown).

**Architecture:** Keep watering decisions on-device in ESPHome packages, with HA as config/input/analytics layer. Use a single HA YAML override profile as source for mutable settings, push values into firmware entities, and expose only operator-meaningful entities in default dashboards. Extend engine/scheduler to support `gallons_target` and `time_target` per zone.

**Tech Stack:** ESPHome YAML packages, Home Assistant packages/templates/automations, Lovelace dashboard YAML, Markdown docs.

---

### Task 1: Normalize Config Schema and HA Profile Mapping

**Files:**
- Create: `homeassistant/packages/nedorachio_config.yaml`
- Modify: `docs/superpowers/specs/2026-05-11-ha-exposure-and-config-design.md`
- Modify: `homeassistant/packages/nedorachio.yaml`
- Test: `homeassistant/packages/nedorachio.yaml`

- [ ] **Step 1: Create canonical HA config profile file**

```yaml
# homeassistant/packages/nedorachio_config.yaml
nedorachio:
  version: 1
  global:
    watering_window:
      start: "23:00"
      end: "09:00"
      timezone: "America/New_York"
    attempt_cooldown_minutes: 20
    blackout:
      weekdays: []
      dates: []
      ranges: []
  zones:
    1:
      enabled: true
      mode: gallons_target
      goal_gallons_per_cycle: 100
      cycle_gallons: 50
      soak_minutes: 15
      minimum_interval_hours: 72
      minimum_running_psi: 20
      minimum_running_psi_grace_seconds: 600
```

- [ ] **Step 2: Add HA variables/parser block in `nedorachio.yaml` (failing state first)**

```yaml
# homeassistant/packages/nedorachio.yaml (top-level variables in automation)
variables:
  cfg: "{{ state_attr('sensor.nedorachio_config', 'raw') }}"
  # intentionally reference yet-to-be-added parser macro to force a check failure first
  parsed: "{{ nedorachio_parse_cfg(cfg) }}"
```

- [ ] **Step 3: Run config check and verify failure**

Run: `python -m homeassistant --script check_config -c .`
Expected: FAIL with undefined macro/entity reference for `nedorachio_parse_cfg` or profile source.

- [ ] **Step 4: Implement real parser/apply scaffolding**

```yaml
# homeassistant/packages/nedorachio.yaml
script:
  nedorachio_apply_config_profile:
    alias: "Nedorachio: apply config profile"
    sequence:
      - variables:
          cfg: "{{ states('sensor.nedorachio_config_json') | from_json(default={}) }}"
      - choose:
          - conditions: "{{ cfg.get('nedorachio') is not none }}"
            sequence:
              - service: number.set_value
                target:
                  entity_id: number.nedorachio_irrigation_controller_attempt_cooldown_minutes
                data:
                  value: "{{ cfg.nedorachio.global.attempt_cooldown_minutes | float(20) }}"
```

- [ ] **Step 5: Re-run config check**

Run: `python -m homeassistant --script check_config -c .`
Expected: PASS (or no parser/macro-related errors).

- [ ] **Step 6: Commit**

```bash
git add homeassistant/packages/nedorachio_config.yaml homeassistant/packages/nedorachio.yaml docs/superpowers/specs/2026-05-11-ha-exposure-and-config-design.md
git commit -m "feat: add canonical HA profile schema and apply scaffold"
```

### Task 2: Add New Tunables and Naming Consistency in Firmware

**Files:**
- Modify: `firmware/packages/04-tunables.yaml`
- Modify: `firmware/packages/06-schedule.yaml`
- Modify: `firmware/packages/05-engine.yaml`
- Test: `firmware/nedorachio.yaml`

- [ ] **Step 1: Add failing references in schedule/engine for new IDs**

```yaml
# firmware/packages/06-schedule.yaml (temporary reference before definitions)
- lambda: |-
    float cooldown = id(attempt_cooldown_minutes).state;
```

- [ ] **Step 2: Run ESPHome config to verify failure**

Run: `cd firmware && esphome config nedorachio.yaml`
Expected: FAIL with unknown ID `attempt_cooldown_minutes` (and other renamed IDs).

- [ ] **Step 3: Define new IDs and defaults in tunables**

```yaml
# firmware/packages/04-tunables.yaml
number:
  - platform: template
    name: "Attempt cooldown minutes"
    id: attempt_cooldown_minutes
    optimistic: true
    restore_value: true
    initial_value: 20
    min_value: 0
    max_value: 1440
    step: 1
    unit_of_measurement: "min"

  - platform: template
    name: "Zone 1 goal gallons per cycle"
    id: zone_1_goal_gallons_per_cycle
    optimistic: true
    restore_value: true
    initial_value: 100
    min_value: 1
    max_value: 10000
    step: 1
```

- [ ] **Step 4: Rename flow/runtime keys internally for consistency**

```yaml
# firmware/packages/04-tunables.yaml
  - platform: template
    name: "Zone 1 minimum flow gpm"
    id: zone_1_minimum_flow_gpm
    # ...
  - platform: template
    name: "Zone 1 maximum flow gpm"
    id: zone_1_maximum_flow_gpm
    # ...
  - platform: template
    name: "Maximum runtime minutes"
    id: maximum_runtime_minutes
    # global per-attempt runtime safety cap
```

- [ ] **Step 5: Re-run ESPHome config**

Run: `cd firmware && esphome config nedorachio.yaml`
Expected: PASS with no unknown ID errors for new tunables.

- [ ] **Step 6: Commit**

```bash
git add firmware/packages/04-tunables.yaml firmware/packages/05-engine.yaml firmware/packages/06-schedule.yaml
git commit -m "refactor: add cooldown and normalized per-zone tunable IDs"
```

### Task 3: Implement Global Watering Window + Blackout Evaluation

**Files:**
- Modify: `firmware/packages/06-schedule.yaml`
- Modify: `homeassistant/packages/nedorachio.yaml`
- Test: `firmware/nedorachio.yaml`

- [ ] **Step 1: Add schedule-allowed sensor with failing placeholder logic**

```yaml
# firmware/packages/06-schedule.yaml
binary_sensor:
  - platform: template
    name: "Schedule allowed now"
    id: schedule_allowed_now
    lambda: |-
      return false;  // placeholder to be replaced
```

- [ ] **Step 2: Validate baseline compile**

Run: `cd firmware && esphome config nedorachio.yaml`
Expected: PASS (placeholder is valid but behavior intentionally wrong).

- [ ] **Step 3: Implement real window + blackout logic**

```yaml
# firmware/packages/06-schedule.yaml (lambda excerpt)
lambda: |-
  // evaluate cross-midnight [start,end), then apply blackout weekday/date checks
  bool in_window = (start_min < end_min)
      ? (now_min >= start_min && now_min < end_min)
      : (now_min >= start_min || now_min < end_min);
  if (!in_window) return false;
  if (id(blackout_today).state) return false;
  return true;
```

- [ ] **Step 4: Use `schedule_allowed_now` gate in evaluator**

```yaml
# firmware/packages/06-schedule.yaml
- lambda: |-
    if (!id(schedule_allowed_now).state) return;
```

- [ ] **Step 5: Re-run ESPHome config**

Run: `cd firmware && esphome config nedorachio.yaml`
Expected: PASS with schedule gate compiled.

- [ ] **Step 6: Commit**

```bash
git add firmware/packages/06-schedule.yaml homeassistant/packages/nedorachio.yaml
git commit -m "feat: add global schedule-allowed gate with blackout support"
```

### Task 4: Implement Per-Zone Dual Mode and Gallons-Target Runtime

**Files:**
- Modify: `firmware/packages/05-engine.yaml`
- Modify: `firmware/packages/06-schedule.yaml`
- Modify: `firmware/packages/07-stats.yaml`
- Test: `firmware/nedorachio.yaml`

- [ ] **Step 1: Add per-zone mode and cycle accumulator globals (failing references first)**

```yaml
# firmware/packages/05-engine.yaml
globals:
  - id: zone_1_cycle_delivered_gallons
    type: float
    restore_value: true
    initial_value: '0.0'
```

- [ ] **Step 2: Run compile and verify missing-ID failures**

Run: `cd firmware && esphome config nedorachio.yaml`
Expected: FAIL on any referenced but undefined zone mode/goal/cycle gallons IDs.

- [ ] **Step 3: Implement mode branching in schedule fire path**

```yaml
# firmware/packages/06-schedule.yaml (excerpt)
if (id(zone_1_mode).state == 0) {
  // time_target path
  id(staged_total_minutes) = id(zone_1_total_minutes).state;
} else {
  // gallons_target path
  id(staged_goal_gallons) = id(zone_1_goal_gallons_per_cycle).state;
  id(staged_cycle_gallons) = id(zone_1_cycle_gallons).state;
}
```

- [ ] **Step 4: Implement gallons chunk + soak loop with carry-forward**

```yaml
# firmware/packages/05-engine.yaml (pseudo-structure)
- id: run_one_zone_gallons_target
  then:
    - while:
        condition:
          lambda: 'return id(zone_cycle_delivered) < id(zone_goal_gallons);'
        then:
          - script.execute: run_chunk_until_cycle_gallons_or_fault
          - if:
              condition:
                lambda: 'return id(zone_cycle_delivered) >= id(zone_goal_gallons);'
              then:
                - break
          - delay: !lambda 'return (uint32_t)(id(zone_soak_minutes).state * 60000);'
```

- [ ] **Step 5: Re-run ESPHome config**

Run: `cd firmware && esphome config nedorachio.yaml`
Expected: PASS with dual-mode scripts and IDs resolved.

- [ ] **Step 6: Commit**

```bash
git add firmware/packages/05-engine.yaml firmware/packages/06-schedule.yaml firmware/packages/07-stats.yaml
git commit -m "feat: add per-zone dual scheduling and gallons-target execution"
```

### Task 5: Add PSI Start Guards and In-Run Grace-Timed Low-PSI Stop

**Files:**
- Modify: `firmware/packages/05-engine.yaml`
- Modify: `firmware/packages/04-tunables.yaml`
- Test: `firmware/nedorachio.yaml`

- [ ] **Step 1: Add pre-flight PSI guard assertions**

```yaml
# firmware/packages/05-engine.yaml (pre-flight)
if (psi < id(zone_1_start_minimum_psi).state || psi > id(zone_1_start_maximum_psi).state) {
  id(pre_flight_passed) = false;
  id(pre_flight_reason) = "start_psi_out_of_bounds";
  return;
}
```

- [ ] **Step 2: Add in-run low-PSI grace cancellation**

```yaml
# firmware/packages/05-engine.yaml (in-run monitor)
if (psi < id(zone_1_minimum_running_psi).state) {
  low_psi_ms += tick_ms;
  if (low_psi_ms >= id(zone_1_minimum_running_psi_grace_seconds).state * 1000) {
    id(run_cancel_cause) = "low_pressure";
    id(cancel_requested) = true;
  }
} else {
  low_psi_ms = 0;
}
```

- [ ] **Step 3: Run compile check**

Run: `cd firmware && esphome config nedorachio.yaml`
Expected: PASS with PSI guards and grace timing.

- [ ] **Step 4: Commit**

```bash
git add firmware/packages/04-tunables.yaml firmware/packages/05-engine.yaml
git commit -m "feat: enforce per-zone psi start bounds and low-psi grace stop"
```

### Task 6: Apply Global Attempt Cooldown and Manual Bypass Rule

**Files:**
- Modify: `firmware/packages/06-schedule.yaml`
- Modify: `firmware/packages/05-engine.yaml`
- Test: `firmware/nedorachio.yaml`

- [ ] **Step 1: Add last-attempt timestamp state**

```yaml
# firmware/packages/06-schedule.yaml
globals:
  - id: last_non_completed_attempt_epoch
    type: uint32_t
    restore_value: true
    initial_value: '0'
```

- [ ] **Step 2: Gate automatic attempts by cooldown**

```yaml
# firmware/packages/06-schedule.yaml
uint32_t cooldown_s = (uint32_t)(id(attempt_cooldown_minutes).state * 60.0f);
if (id(last_non_completed_attempt_epoch) > 0 && now_epoch < id(last_non_completed_attempt_epoch) + cooldown_s) return;
```

- [ ] **Step 3: Ensure manual starts bypass cooldown**

```yaml
# firmware/packages/05-engine.yaml
// manual run path does not consult attempt cooldown
id(run_origin) = "manual";
```

- [ ] **Step 4: Compile check**

Run: `cd firmware && esphome config nedorachio.yaml`
Expected: PASS with cooldown logic.

- [ ] **Step 5: Commit**

```bash
git add firmware/packages/05-engine.yaml firmware/packages/06-schedule.yaml
git commit -m "feat: add global automatic attempt cooldown with manual bypass"
```

### Task 7: Reduce HA Surface and Add Gallons Chart Dashboard

**Files:**
- Modify: `homeassistant/packages/nedorachio_dashboard.yaml`
- Modify: `homeassistant/packages/nedorachio.yaml`
- Modify: `firmware/packages/02-zones.yaml`
- Modify: `firmware/packages/04-tunables.yaml`
- Test: `homeassistant/packages/nedorachio_dashboard.yaml`

- [ ] **Step 1: Hide non-operator controls from default dashboard**

```yaml
# homeassistant/packages/nedorachio_dashboard.yaml
entities:
  - entity: switch.nedorachio_irrigation_controller_zone_1
  - entity: switch.nedorachio_irrigation_controller_zone_2
  - entity: switch.nedorachio_irrigation_controller_zone_3
  - entity: switch.nedorachio_irrigation_controller_zone_4
  - entity: switch.nedorachio_irrigation_controller_fallback_schedule_enabled
```

- [ ] **Step 2: Add cumulative gallons line chart card**

```yaml
# homeassistant/packages/nedorachio_dashboard.yaml
- type: statistics-graph
  title: Gallons Over Time
  days_to_show: 14
  stat_types:
    - change
  entities:
    - sensor.nedorachio_irrigation_controller_total_gallons
    - sensor.nedorachio_irrigation_controller_zone_1_gallons_total
    - sensor.nedorachio_irrigation_controller_zone_2_gallons_total
    - sensor.nedorachio_irrigation_controller_zone_3_gallons_total
    - sensor.nedorachio_irrigation_controller_zone_4_gallons_total
```

- [ ] **Step 3: Add read-only gallons-target analytics entities to dashboard**

```yaml
# homeassistant/packages/nedorachio_dashboard.yaml
- type: entities
  title: Gallons Target Progress
  entities:
    - sensor.nedorachio_irrigation_controller_zone_1_cycle_delivered_gallons
    - sensor.nedorachio_irrigation_controller_zone_1_cycle_remaining_gallons
    - sensor.nedorachio_irrigation_controller_zone_1_cycle_progress_pct
```

- [ ] **Step 4: Run HA config check**

Run: `python -m homeassistant --script check_config -c .`
Expected: PASS and dashboard YAML accepted.

- [ ] **Step 5: Commit**

```bash
git add homeassistant/packages/nedorachio.yaml homeassistant/packages/nedorachio_dashboard.yaml firmware/packages/02-zones.yaml firmware/packages/04-tunables.yaml
git commit -m "feat: trim default HA controls and add gallons analytics dashboard"
```

### Task 8: Documentation and Default Rollout Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-05-11-ha-exposure-and-config-design.md`
- Test: `README.md`

- [ ] **Step 1: Document new default policy and schema fields**

```markdown
## Default schedule policy

- All zones default to `gallons_target`
- `goal_gallons_per_cycle: 100`
- `cycle_gallons: 50`
- `minimum_running_psi: 20`
- `minimum_running_psi_grace_seconds: 600`
```

- [ ] **Step 2: Add operator guide for blackout and cooldown**

```markdown
### Blackout and retry cadence

- Set blackout weekdays/dates/ranges in `homeassistant/packages/nedorachio_config.yaml`
- Automatic non-completed attempts honor `attempt_cooldown_minutes`
- Manual starts bypass cooldown but still enforce safety gates
```

- [ ] **Step 3: Verify firmware and HA config one final time**

Run: `cd firmware && esphome config nedorachio.yaml && cd .. && python -m homeassistant --script check_config -c .`
Expected: PASS for both checks.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/superpowers/specs/2026-05-11-ha-exposure-and-config-design.md
git commit -m "docs: document gallons-target defaults blackout and cooldown behavior"
```

## Self-Review

1. **Spec coverage:** The tasks cover minimal HA controls, bidirectional sensors, YAML profile, dual mode scheduling, gallons charting, cooldown/manual bypass, PSI guards, and defaults.
2. **Placeholder scan:** No TBD/TODO placeholders remain; each task includes explicit file paths and command expectations.
3. **Type consistency:** Time-unit keys use full words (`*_minutes`, `*_hours`, `*_seconds`) and flow/PSI fields use `minimum`/`maximum` consistently.
