# Weekly Gallons Scheduler — Nedorachio

**Date:** 2026-05-25  
**Status:** Approved design, ready for implementation plan  
**Owner:** konstantinraev

## 1. Summary

Replace the per-zone **cadence scheduler** (minimum interval hours + per-cycle gallon target with cycle-and-soak) with a **weekly gallon budget scheduler**. Each enabled zone has a configurable `weekly_goal_gallons`. During allowed watering windows and days, the controller runs **round-robin** among zones that still have a deficit, delivering water in **time-capped attempts** until the weekly goal is met or the window closes.

**Weekly progress is HA-primary:** Home Assistant owns calendar-week gallon accounting (Monday 00:00 reset). The ESP reads HA sensors when online and falls back to a **local NVS shadow** when HA is unreachable, continuing to schedule from last-known values plus on-device flow metering.

Soak, time-based scheduling mode, gallon chunking, and per-cycle cadence are removed.

## 2. Goals

- Water by **weekly gallon budget** per zone, not by fixed time or minimum interval.
- Respect existing **watering window**, **blackout weekdays**, **pre-flight gates**, and **mid-run pressure/flow cancels**.
- **Count partial delivery** toward the weekly total even when a run is cancelled (low pressure, no flow, runtime cap, rain).
- Cap each attempt at a global **`max_attempt_minutes`**; yield to the next eligible zone when the cap is hit.
- Apply a **per-zone cooldown** after every attempt, using a global **`attempt_cooldown_minutes`** duration.
- Use **round-robin** among zones with a weekly deficit.
- Reset weekly accounting on **Monday 00:00** in the configured timezone; **fresh start** (no deficit carry-over).
- Keep irrigation logic on the device; HA provides calendar-week tracking and dashboard visibility.
- Device continues scheduling when HA is offline using local shadow state.

## 3. Non-goals

- Rolling 7-day windows for scheduling (7-day stats may remain on the dashboard for charts only).
- ET-based or weather-adjusted gallon targets.
- Soak / cycle-and-soak (removed entirely).
- Time-based scheduled runs (`total_minutes`, `cycle_minutes`).
- Per-zone `max_attempt_minutes` (global only).
- Deficit carry-over across calendar weeks.
- HA-side delivery-event accumulation automations (gallons still come from device lifetime counters).

## 4. Decisions (locked)

| Topic | Choice |
|---|---|
| Budget period | Calendar week, resets **Monday 00:00** (configured TZ) |
| Week rollover | **Fresh start** — undelivered gallons discarded |
| Target | Per-zone **`weekly_goal_gallons`** |
| Scheduling priority | **Round-robin** among zones with deficit |
| Attempt limit | **Global** `max_attempt_minutes` |
| Cooldown | **Per-zone**, global duration, after **every** attempt |
| Modes | **Gallons-only** — remove time mode, soak, chunking |
| Tracking | **HA-primary** with **local NVS shadow** when offline |
| Reconnect merge | `shadow = max(ha_weekly_delivered, local_shadow)` for same week |

## 5. Architecture

### 5.1 Responsibility split

```
┌─────────────────────────────────────────────────────────────┐
│                     Home Assistant                          │
│  ─ Calendar week baselines (input_number per zone)          │
│  ─ Template sensors: weekly_delivered = lifetime − baseline │
│  ─ Monday 00:00 automation: reset baselines (fresh start)   │
│  ─ Dashboard: weekly progress, tracking_source              │
│  ─ Rain feeder, notifications (unchanged)                   │
│  ─ Last-watering helpers (unchanged)                          │
└──────────────────────────┬──────────────────────────────────┘
                           │ ESPHome native API
                           │  ← read sensor.nedorachio_zone_N_weekly_delivered
                           │  → publish lifetime gallons (existing)
┌──────────────────────────┴──────────────────────────────────┐
│              Nedorachio controller (ESP32)                  │
│  ─ Weekly budget evaluator (replaces cadence evaluator)     │
│  ─ Round-robin zone picker                                  │
│  ─ Gallons run engine (single path, no soak)                │
│  ─ Local shadow (NVS) when HA feed unavailable              │
│  ─ Pre-flight, safety, flow/pressure cancels (unchanged)    │
└─────────────────────────────────────────────────────────────┘
```

### 5.2 HA layer — calendar week tracking

For each zone `N` (1–8):

**Storage:** `input_number.nedorachio_zone_N_week_baseline_gallons` — lifetime total at the most recent Monday 00:00 reset.

**Computed sensor:** `sensor.nedorachio_zone_N_weekly_delivered`:

```
weekly_delivered = zone_N_gallons_lifetime − zone_N_week_baseline_gallons
```

(clamped to ≥ 0)

**Monday 00:00 automation** (configured timezone, default from watering window TZ):

For each enabled zone, set `week_baseline_gallons = current lifetime total`.

This implements fresh-start rollover: delivered resets to 0; last week's shortfall is not carried forward.

**Additional HA template sensors (display):**

- `sensor.nedorachio_zone_N_weekly_remaining` = `max(0, weekly_goal − weekly_delivered)`  
  (`weekly_goal` comes from device config profile mirror or HA helper — see §5.5)
- `sensor.nedorachio_zone_N_weekly_progress_pct`

**Existing rolling 7-day statistics** remain for historical charts on the dashboard. They do **not** drive scheduling.

### 5.3 Device layer — local shadow

Per zone in NVS:

| Field | Purpose |
|---|---|
| `weekly_delivered_shadow` | Delivered gallons for current calendar week |
| `week_id_shadow` | ISO-style week id (year×100 + week number, Monday-based) |
| `lifetime_at_last_sync` | Device lifetime gallons at last HA alignment |
| `last_attempt_epoch` | Per-zone cooldown anchor |
| `last_finished_epoch` | Last time weekly goal was reached (HA sync, unchanged pattern) |

Global NVS:

| Field | Purpose |
|---|---|
| `last_served_zone_id` | Round-robin pointer |
| `tracking_source` | `ha` or `local` (exposed as sensor for dashboard) |

### 5.4 HA → device sync

Mirror the existing `last_watering` pattern in `10-nedorachio-component.yaml`:

```yaml
sensor:
  - platform: homeassistant
    id: ha_zone_1_weekly_delivered
    entity_id: sensor.nedorachio_zone_1_weekly_delivered
    on_value:
      - lambda: 'id(irrigation).on_zone_weekly_delivered(1, (float) x);'
```

Handler `on_zone_weekly_delivered(zone_id, gallons)`:

1. If HA week id matches `week_id_shadow`, set `weekly_delivered_shadow = gallons` and `tracking_source = ha`.
2. If HA week advanced, adopt HA value, update `week_id_shadow`, reset round-robin if needed.

### 5.5 Config profile changes

**Global** (`11-config-profile.yaml`):

```json
{
  "max_attempt_minutes": 30,
  "attempt_cooldown_minutes": 20,
  "watering_window": { "start": "23:00", "end": "09:00", "timezone": "America/New_York" },
  "blackout": { "weekdays": ["thu", "fri"] }
}
```

**Removed from global:** `maximum_runtime_minutes` (replaced by `max_attempt_minutes`).

**Per zone:**

```json
{
  "enabled": true,
  "weekly_goal_gallons": 100,
  "start_minimum_psi": 35,
  "start_maximum_psi": 85,
  "minimum_running_psi": 20,
  "minimum_running_psi_grace_seconds": 60,
  "minimum_flow_gpm": 0.2,
  "maximum_flow_gpm": 12.0
}
```

**Removed per zone:** `mode`, `goal_gallons_per_cycle`, `cycle_gallons`, `soak_minutes`, `minimum_interval_hours`, `total_minutes`, `cycle_minutes`, `soak_minutes`.

Week start is fixed at **Monday 00:00** (not configurable; documented constant).

## 6. Scheduler — weekly budget evaluator

Replaces `cadence_evaluator` / `next_due_zone`. Runs every 60s when idle (same tick rate as today).

### 6.1 Resolve weekly delivered

For each enabled zone:

```
if ha_weekly_feed_valid:
    delivered = ha_weekly_delivered
    tracking_source = ha
else:
    delivered = weekly_delivered_shadow
    tracking_source = local
```

HA feed is valid when `ha_time` is valid **and** the subscribed weekly-delivered sensor has updated within a staleness threshold (e.g. 15 minutes while HA API is connected). Exact threshold is implementation detail; default 15 min.

### 6.2 Week rollover (device-side)

Compute current `week_id` from epoch + timezone (Monday-based ISO week).

If `week_id != week_id_shadow`:

- Set `week_id_shadow = week_id`
- Set `weekly_delivered_shadow = 0` for all zones
- Reset `last_served_zone_id = 0`

This allows offline operation through Monday midnight even if HA hasn't pushed yet.

### 6.3 Eligibility

Zone is eligible when **all** of:

- `enabled`
- `delivered < weekly_goal_gallons`
- `now >= last_attempt_epoch + attempt_cooldown_minutes`
- Inside watering window
- Not a blackout weekday
- `fallback_schedule_enabled` and master enable / no e-stop (unchanged)

### 6.4 Round-robin pick

Among eligible zones, select the next zone after `last_served_zone_id` (wrap 8→1). Skip ineligible zones. If none eligible, idle.

On pick: set `last_served_zone_id = picked`, run pre-flight, start attempt.

### 6.5 Plan readout

Replace cadence-based `scheduled_next_epoch` with simpler readouts:

- `zone_N_weekly_delivered` (from resolved source)
- `zone_N_weekly_remaining`
- `zone_N_weekly_goal_met` (bool)
- `next_eligible_epoch` (earliest time this zone can attempt again = cooldown end, if still in deficit)

## 7. Run engine

Single code path: `_run_zone_weekly_gallons` (replaces time mode and cycle-and-soak gallons mode).

### 7.1 Start conditions

- Pre-flight passed (unchanged: rain, pressure static, alarms, time sync for scheduled runs).
- Start pressure in `[start_minimum_psi, start_maximum_psi]` for scheduled runs (unchanged).

### 7.2 Run loop

Open valve. Each tick:

- Integrate flow → increment lifetime gallons and `weekly_delivered_shadow`.
- Stop when **any** condition is true:

| Condition | Outcome |
|---|---|
| `delivered >= weekly_goal_gallons` | Complete; stamp `last_finished_epoch`; zone done for week |
| Attempt duration ≥ `max_attempt_minutes` | Stop; partial counts; cooldown |
| Pressure/flow fault | Cancel; partial counts; cooldown |
| Rain / e-stop / user cancel | Cancel; partial counts; cooldown |

No soak phase. No gallon chunk limits.

### 7.3 After every attempt

- Stamp `last_attempt_epoch = now` for that zone.
- Persist shadow to NVS.
- Publish lifetime gallons (existing sensor path).
- Set `last_run_outcome` (`completed`, `cancelled_*`, `attempt_cap`).

### 7.4 Manual runs

Manual zone-on (HA switch or local button) uses the same gallons engine but:

- May use separate `manual_run_max_minutes` cap (existing local button behavior) **or** share `max_attempt_minutes` — implementation plan should pick one; default: share `max_attempt_minutes` for simplicity unless local button cap already exists as distinct entity (keep `local_button_max_min` for button-only cap).
- Manual runs **do** count toward weekly delivered (user expectation: water used is water used).
- Cooldown still applies after manual attempts.

## 8. Offline behavior & reconciliation

### 8.1 While HA offline

- Device sets `tracking_source = local`.
- Scheduler uses `weekly_delivered_shadow`.
- Flow meter continues updating lifetime + shadow.
- Week rollover handled locally (§6.2).

### 8.2 On HA reconnect

For each zone, same calendar week:

```
weekly_delivered_shadow = max(ha_weekly_delivered, weekly_delivered_shadow)
tracking_source = ha
lifetime_at_last_sync = current lifetime
```

If HA week id > device week id: adopt HA values (fresh start from HA's Monday reset).

If device week id > HA week id (device saw Monday first): device shadow stands until HA catches up; do not regress shadow downward.

### 8.3 Staleness

If HA API is connected but weekly-delivered sensors stop updating (HA restart, template error), fall back to local shadow after staleness threshold. Expose `tracking_source = local` on device.

## 9. Migration

### 9.1 Config profile

| Old field | New field |
|---|---|
| `goal_gallons_per_cycle` | `weekly_goal_gallons` (same numeric value initially) |
| `maximum_runtime_minutes` | `max_attempt_minutes` |
| `cycle_gallons`, `soak_minutes`, `minimum_interval_hours` | removed |
| `mode: "gallons_target"` | removed (only mode) |

### 9.2 Runtime state

- `cycle_delivered_gallons` → replaced by `weekly_delivered_shadow` (reset to 0 on first boot with new firmware; HA baseline reset on first Monday or manual one-time migration automation).
- Recommend one-time HA automation on upgrade: set all `week_baseline_gallons = current lifetime` so weekly delivered starts at 0.

### 9.3 Python simulation library

Update `src/nedorachio/` to mirror firmware:

- `schedule.py`: replace `next_due_zone` / `compute_zone_plans` with weekly budget + round-robin.
- `controller.py`: remove soak/time paths; single weekly gallons run.
- `models.py`: update config and runtime types.
- `profile_bridge.py`: map new config profile schema.

### 9.4 Contract tests

Update `tests/controller/ha_integration_contract.py`:

- Add checks for `week_baseline_gallons` helpers and `weekly_delivered` template sensors.
- Add Monday reset automation id.
- Add firmware `on_zone_weekly_delivered` handlers.
- Remove prohibitions on calendar-week reset where they conflict (old rule preferred rolling 7d for scheduling; new design uses calendar week for scheduling, rolling 7d for charts only).
- Remove soak/cadence/interval contract requirements.

## 10. Home Assistant dashboard

Replace or supplement "Next watering" / cadence display with:

- **Weekly progress** per zone: `delivered / goal` gal
- **Weekly remaining** gal
- **Tracking source** (`ha` / `local`)
- **Next eligible** (cooldown end timestamp)

Keep gallons history graph (7-day rolling) under a "History" section.

Remove references to soak phase, cycle cadence, minimum interval.

## 11. Testing

### 11.1 Unit tests (`tests/unit/`)

- Week id computation (Monday boundary, timezone).
- Round-robin selection with cooldown filtering.
- Shadow increment on partial delivery.
- Week rollover resets shadow.
- Reconnect merge (`max` rule).

### 11.2 Controller scenarios (`tests/controller/`)

- Zone reaches weekly goal mid-week → excluded from round-robin until Monday.
- Attempt cap → partial delivery, cooldown, next zone runs.
- Pressure cancel → gallons kept, cooldown, retry later.
- Offline week: shadow-only scheduling across multiple attempts.
- HA reconnect merge after offline watering.
- Monday reset fresh start (no carry-over).
- Round-robin fairness across 4 zones with equal deficits.

### 11.3 HA integration contract

Static checks for new helpers, sensors, automations, firmware subscriptions.

## 12. Files to change (implementation reference)

| Area | Files |
|---|---|
| Firmware engine | `firmware/components/nedorachio/engine.cpp`, `engine.h` |
| Firmware schedule | `firmware/components/nedorachio/schedule.cpp`, `schedule.h` |
| Firmware component | `nedorachio_component.cpp`, `10-nedorachio-component.yaml` |
| Config profile | `firmware/packages/11-config-profile.yaml` |
| Python lib | `src/nedorachio/{models,schedule,controller,config,profile_bridge,runtime_state}.py` |
| HA package | `homeassistant/packages/nedorachio.yaml` |
| HA dashboard | `homeassistant/packages/nedorachio-dashboard.yaml` |
| Tests | `tests/unit/*`, `tests/controller/*` |
| Docs | `README.md` (scheduling model section) |

## 13. Open implementation notes

- **Manual run cap:** Prefer keeping existing `local_button_max_min` / `manual_run_max_minutes` for physical button only; scheduled attempts use `max_attempt_minutes`.
- **HA weekly_goal display:** Mirror from device config profile text/number entities if exposed, or duplicate in HA package as read-only reference aligned with flash config.
- **First-boot baseline:** Document manual step or ship upgrade automation to initialize baselines so weekly delivered doesn't show lifetime total on day one.
