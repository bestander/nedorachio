# Nedorachio HA Exposure and Config Design

Date: 2026-05-11
Status: Draft for review
Owner: Nedorachio project

## 1) Problem and Goals

Current HA exposure has drifted into an awkward middle ground: too many low-value controls are visible, while key operational analytics and a clear config contract are not first-class.

Goals:

- Keep controller autonomy as the primary design rule: firmware owns watering decisions.
- Keep HA controls minimal and obvious in day-to-day use.
- Expose complete, meaningful sensor signals in both directions (HA -> device and device -> HA) for decisioning and observability.
- Move mutable configuration to YAML-based profile overrides in HA, while retaining safe defaults in firmware.
- Provide richer "past + future" watering analytics without exposing low-level engine internals.

Non-goals:

- Re-implementing scheduling logic inside HA.
- Making every firmware variable an HA entity.
- Removing debug capabilities entirely (they can remain in an opt-in diagnostics surface).

## 2) Design Principles

1. Device-first autonomy
   - Firmware runs scheduling, pre-flight gates, retries, and runtime safety locally.
   - HA outages must not break core safety behavior.

2. Minimal operator controls
   - Only controls required for normal operation should be writable in default HA UX.

3. Bidirectional sensor model
   - "Sensor" means any control-loop signal, regardless of origin.
   - Signals consumed by firmware and signals produced by firmware are both first-class.

4. Default-deny exposure
   - Entities are hidden unless they serve operator action or system understanding.

## 3) Home Assistant Entity Contract

### 3.1 Writable controls (default surface)

Expose only:

- Per-zone valve controls (`zone_1`..`zone_N`, domain can remain `switch` or migrate to `valve`)
- Master schedule enable/disable control (`fallback_schedule_enabled`, user-facing name "Master schedule")

Everything else is read-only in default HA surfaces.

### 3.2 Read-only sensor/analytics entities

Expose these user-meaningful categories:

- **Control-loop inputs (HA -> device):**
  - Forecast/weather signal(s) used by decisions
  - Observed rain aggregate(s) used by rain hold logic
  - Time reference validity/effective-now signal
  - Temperature/current+forecast inputs used in watering decisions

- **Control-loop telemetry (device -> HA):**
  - Pressure (PSI)
  - Flow rate (GPM)
  - Flow totals/pulses
  - Rain sensor state

- **Decision state and outcomes (device -> HA):**
  - Current phase / running zone / progress / remaining time
  - Next due zone and next-run timestamps
  - Last run started/finished/outcome
  - Per-zone last-finished and next-run readouts
  - Failure/cancel reason and retry result summaries

### 3.3 Hidden/internal by default

Hide low-level helper state unless it directly powers the user-facing entities above:

- Scratch/intermediate phase timing variables
- Internal gate toggles and implementation details
- Raw helper globals/counters without operator value

Optional: keep a separate opt-in diagnostics package/dashboard for commissioning and debugging.

## 4) Configuration Model (Hybrid)

Selected model: **hybrid**.

- Firmware defines safe defaults in package YAML (`04-tunables.yaml`, related packages).
- HA provides optional YAML overrides as a profile.
- HA applies overrides to device entities via automation/script.
- If HA overrides are absent or invalid, firmware defaults remain in effect.

This preserves autonomy while enabling centralized configuration edits without reflashing firmware.

## 5) YAML Override Profile

Proposed file:

- `homeassistant/packages/nedorachio_config.yaml`

Proposed schema:

```yaml
nedorachio:
  version: 1
  global:
    watering_window:
      start: "23:00"
      end: "09:00"
      timezone: "America/New_York"
    attempt_cooldown_minutes: 20   # global wait between automatic attempts after a non-completed attempt
    maximum_runtime_minutes: 60    # global per-attempt runtime safety cap
    blackout:
      weekdays: ["mon"]            # mon,tue,wed,thu,fri,sat,sun
  zones:
    1:
      enabled: true
      mode: gallons_target         # gallons_target | time_target
      goal_gallons_per_cycle: 100
      cycle_gallons: 50            # used in gallons_target mode; soak after each delivered chunk
      soak_minutes: 15             # used in both modes
      minimum_interval_hours: 72
      total_minutes: 20            # used in time_target mode
      cycle_minutes: 10            # used in time_target mode
      start_minimum_psi: 35        # pre-flight lower PSI bound required to start a run
      start_maximum_psi: 85        # pre-flight upper PSI bound required to start a run
      minimum_running_psi: 20      # in-run PSI floor; below this for grace_seconds cancels attempt
      minimum_running_psi_grace_seconds: 600 # low-PSI grace timer before cancelling attempt
      minimum_flow_gpm: 0.2
      maximum_flow_gpm: 12.0
```

Notes:

- Zone map is sparse-friendly (only zones present are overridden).
- Version field allows future schema migration without silent breakage.
- Watering window supports cross-midnight intervals (`start > end`, e.g. `23:00` -> `09:00`).
- Blackout weekdays block new schedule starts, but do not discard already accumulated gallons progress.
- Date/range blackout support is intentionally deferred.
- Default rollout baseline: all zones start in `gallons_target` mode with `goal_gallons_per_cycle: 100`, `cycle_gallons: 50`, `minimum_running_psi: 20`, and `minimum_running_psi_grace_seconds: 600` unless explicitly overridden.
- `maximum_runtime_minutes` is global and applies to each individual ON attempt for any zone.

## 6) Config Apply Pipeline

Implementation intent:

1. HA startup trigger runs "apply profile" script.
2. Optional manual "re-apply config" action reruns same script.
3. Script performs ordered writes:
   - Global window + blackout-weekday + attempt-cooldown values
   - Per-zone timing/tolerance values
   - Zone enable mask/state
4. Validation is done before write:
   - Range checks and type checks
   - Invalid keys skipped and logged
5. Idempotent operation:
   - Re-running with same profile yields same resulting state.

Failure behavior:

- Partial apply should not stop controller operation.
- Last valid device values remain active.
- HA should expose apply status summary (`ok` / `partial` / `error`) and reason text.
- Invalid blackout weekday entries are ignored individually with structured warning logs.

## 7) Analytics Model

Use a hybrid analytics strategy:

- Persist compact summaries/counters on device for autonomy and continuity.
- Emit structured HA events for rich historical analysis and timeline rendering.

For charting, expose cumulative counters as first-class analytics entities:

- Per-zone cumulative gallons (`zone_N_gallons_total`)
- System cumulative gallons (`total_gallons`)

These are the primary sources for "gallons over time" line charts and zone comparison.

Suggested event types:

- `run_attempt_started`
- `run_attempt_skipped` (with skip reason)
- `run_attempt_failed` (with failure reason)
- `run_attempt_completed`

Suggested analytics outputs:

- Next watering (global + per-zone)
- Last watering (global + per-zone)
- Failed attempts (24h/7d counts + latest reasons)
- Retry outcomes (used/exhausted/succeeded-after-retry)
- Cumulative gallons over time (per-zone and total)

## 8) Chart/Widget Strategy

Home Assistant supports this directly; no synthetic "one sensor per minute" approach is required.

Recommended initial widget:

- One line chart card showing:
  - `total_gallons` cumulative line
  - `zone_1_gallons_total`..`zone_N_gallons_total` as separate lines (toggleable)

Implementation options:

- Native HA: `statistics-graph` (simple, reliable)
- Optional enhanced card later: `apexcharts-card` for better overlays and styling

Future overlays (deferred but planned):

- PSI time-series on secondary axis
- Historical temperature on secondary axis

Data granularity guidance:

- Keep normal sensor update cadence and rely on HA recorder/statistics.
- Avoid creating separate per-minute synthetic entities; this adds noise and storage churn.
- If finer diagnostics are needed later, increase raw telemetry cadence selectively for specific metrics.

## 9) Scheduling Mode Extension: Per-Zone Dual Mode

Add per-zone scheduling strategy with two modes:

- `time_target` (existing total/cycle/soak model)
- `gallons_target` (new)

### 9.1 Gallons-target zone semantics

Each zone in `gallons_target` mode defines:

- `goal_gallons_per_cycle` (e.g. 100 gal)
- `cycle_gallons` (e.g. 25 gal ON segment before soak)
- `soak_minutes` (pause duration between ON segments)
- `minimum_interval_hours` (e.g. every 72h)

Runtime behavior:

- When a zone becomes eligible, watering starts and gallons-delivered is accumulated.
- Watering is chunked: each ON segment runs until `cycle_gallons` is delivered, then controller soaks for `soak_minutes`.
- If a run is interrupted/fails (pressure/flow/rain/cancel), delivered gallons remain credited.
- Controller continues with additional attempts while schedule is allowed and safety gates pass.
- Cycle completes only when accumulated gallons >= goal.
- On completion, cycle accumulator resets to 0 and next interval timer starts from completion time.
- If window closes or blackout begins, no new attempt starts until schedule becomes allowed again.

PSI safety behavior:

- Pre-flight start gate requires PSI inside zone bounds: `start_minimum_psi <= psi <= start_maximum_psi`.
- During ON segments, if `psi < minimum_running_psi` continuously for `minimum_running_psi_grace_seconds`, current attempt is cancelled.
- Any gallons delivered before cancellation remain credited to the zone cycle accumulator.

This is explicitly a rolling cycle bucket model (carry-forward partial gallons across attempts).

### 9.2 Failure/retry interaction

- Partial delivery is never lost.
- There is no fixed retry-count limit in schema.
- Additional attempts remain safety-gated; attempts only happen when pre-flight passes and schedule is allowed.
- Automatic re-attempts are delayed by global `attempt_cooldown_minutes` after each non-completed attempt.
- Manual zone starts bypass `attempt_cooldown_minutes` but still enforce safety gates.
- Outcome states distinguish:
  - `partial_progress`
  - `goal_reached`
  - `cycle_abandoned` (if manually reset)
- Low-PSI stop behavior matches low-flow behavior: grace-timed cancel with partial gallons retained.

### 9.3 New analytics for gallons-target mode

Per-zone read-only outputs:

- `zone_N_cycle_goal_gallons`
- `zone_N_cycle_chunk_gallons`
- `zone_N_cycle_delivered_gallons`
- `zone_N_cycle_remaining_gallons`
- `zone_N_cycle_progress_pct`
- `zone_N_mode` (`time_target` or `gallons_target`)

## 10) Dashboard Information Architecture

Single primary dashboard view with minimal controls and rich read-only context:

1. Controls card
   - Zone switches
   - Master schedule switch
2. Now/Next card
   - Current phase, running zone, progress
   - Next due zone / next run timestamp
3. Zone status card/table
   - Per-zone last finished, next run, interval
4. Reliability card
   - Recent failed attempts, reasons, carry-forward progress, 24h/7d health indicators

Diagnostics controls/entities are not shown on the default operator dashboard.

## 11) Migration Strategy

1. Define final exposed-entity whitelist and hide policy.
2. Add/rename entities to match contract (preserving unique IDs where possible).
3. Introduce HA config profile file and apply script.
4. Move existing mutable settings out of day-to-day UI into YAML profile.
5. Add event emission and failure summary sensors.
6. Add per-zone dual schedule mode and gallons-cycle accumulator state.
7. Add cumulative-gallons chart widget and per-zone analytics card.
8. Replace dashboard with minimal-controls + analytics layout.
9. Validate behavior under:
   - Normal operation
   - HA restart/offline
   - Invalid profile values
   - Sensor feed staleness
   - Partial gallons carry-forward across failed attempts
   - Goal completion and interval reset behavior

## 12) Risks and Mitigations

- Risk: YAML/profile complexity in HA.
  - Mitigation: strict schema, validation, clear logs, profile versioning.
- Risk: temporary mismatch between HA profile and device runtime state.
  - Mitigation: deterministic apply ordering and explicit apply status entity.
- Risk: losing debug visibility.
  - Mitigation: separate opt-in diagnostics package/dashboard.
- Risk: mode complexity (time-target + gallons-target coexistence).
  - Mitigation: explicit per-zone mode sensor, strict validation, and clear outcome semantics.
- Risk: blackout weekday schema mistakes causing unexpected blocks.
  - Mitigation: strict parser, per-entry validation logs, and read-only "schedule_allowed_now" visibility sensor.

## 13) Acceptance Criteria

This design is complete when:

- Default HA controls are limited to per-zone valves + master schedule.
- Control-loop sensors are exposed bidirectionally where meaningful.
- Non-meaningful internals are hidden by default.
- HA YAML override profile can safely apply per-zone and global config.
- Dashboard shows clear next/last/failed analytics without control clutter.
- Dashboard provides cumulative gallons line chart for per-zone and total usage.
- Gallons-target zones carry partial progress across failures/retries until goal completion.
- Device remains autonomous and safe when HA is unavailable.
