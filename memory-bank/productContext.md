# Product Context: HIL Scheduler

## Why This Exists
The system closes the operational gap between market/control schedules and plant-level setpoint execution for two battery assets. It allows hardware-in-the-loop operation with equivalent control flows in local emulation and remote transport.

## Primary Users
1. Operators running live tests and needing safe controls.
2. Engineers validating schedule logic, telemetry, and API integration.

## Core User Outcomes
1. Start and stop dispatch safely per plant.
2. Pause/resume scheduler setpoint sending per plant without necessarily stopping the plant.
3. Record clean per-plant measurement sessions without mixed datasets.
4. Adjust manual overrides without disrupting API base schedule ingestion; switch transport mode with guarded transitions.
5. See real-time status for schedule freshness, API posting health, and commanded setpoint write outcomes.
6. Trigger high-impact fleet-wide start/stop actions with explicit confirmation.
7. Run API-connected read-only tests by disabling measurement posting at runtime.
8. Browse historical recorded measurements across days and export cropped data/plots for analysis.

## Product Behavior
### Scheduling
- API schedules are fetched per plant from one market response and form the dispatch base.
- Manual schedules are managed as four independent override series (`LIB P`, `LIB Q`, `VRFB P`, `VRFB Q`) with per-series active/inactive merge toggles.
- Manual editor enforces a terminal `end` breakpoint for non-empty schedules; runtime stores/sends this as a terminal duplicate-value row so manual override stops at the chosen end time.
- Enabled manual override series overwrite the corresponding API signal in the effective dispatch schedule.

### Dispatch
- Dispatch enable is per plant through `scheduler_running_by_plant`.
- Dispatch send control is now independently exposed in the Status tab (`Sending` / `Paused`) and only controls whether scheduler setpoints are written.
- Dashboard start/stop controls submit intents; runtime control engine executes the flow.
- Start enables/stops the plant control path but does not auto-enable scheduler sending.
- Start resolves immediate setpoint selection from the merged effective schedule (API base + enabled manual overrides); the initial write is sent only if dispatch sending is enabled, otherwise it is skipped and surfaced in status.
- Pausing dispatch intentionally freezes the last setpoint already in the plant (no automatic zeroing on pause).
- Stop executes safe-stop flow (gate off, zero setpoints, decay wait, disable).

### Recording
- Recording enable is per plant through `measurements_filename_by_plant`.
- Measurement agent owns boundary insertion, buffering, tolerance-based compression, and flushes.
- Rows are routed by row timestamp to daily per-plant files.

### Observability
- API tab shows fetch status (today/tomorrow) plus measurement posting telemetry.
- API tab includes a runtime posting toggle (`Enabled`/`Disabled`) for session-scoped read-only testing.
- API measurement posting toggle gates posting of actual measurements regardless of manual override usage.
- Status tab (renamed from `Status & Plots`) keeps inline API summary with today/tomorrow fetched-point counts for both plants.
- Status tab plant-state controls/status now render from cached runtime-published plant observed state (no direct dashboard Modbus polling for control/status paths).
- Status tab now separates physical plant state, control transition state, and dispatch send/paused state per plant.
- Status tab now shows the latest commanded/sent setpoint write info per plant (P/Q, timestamp, source, status/error) from runtime-published dispatch-write status cache.
- Status tab live plots are intentionally limited to immediate context (local current day + next day) for both schedule and measurements; plant figures include a current-time vertical marker for operator orientation.
- Plant figures now include a dedicated voltage subplot (`kV`) in both Status and historical `Plots` tabs.
- Historical `Plots` tab provides measurement browsing from `data/*.csv` with a full-range timeline, range slider, per-plant CSV/PNG exports, and recorded P/Q setpoint overlays from measurement rows.
- Logs tab exposes a live `Today` view (tail of the current date log file) and selectable historical log files.

## UX Intent
1. Clear separation between plant control, dispatch-send control, and recording control.
2. Explicit transition states (`starting`, `running`, `stopping`, `stopped`, `unknown`) with immediate click feedback and server-authoritative transition persistence until Modbus confirmation.
3. Safe confirmation flows before transport changes and fleet actions; manual override edits should be direct and low-friction.
4. Stable plot interactions during periodic refresh.
5. Historical browsing controls should preserve context (range selection) while new files appear.
6. Status-tab plots should avoid long-running-session clutter and present only the immediate past/future operating window.
7. Voltage trends for low-voltage assets should remain readable without manual zoom (tight y-scale around observed values).

## Critical Workflows
### Start Plant Dispatch
1. User starts plant card.
2. Dashboard immediately shows a temporary `starting` feedback state and enqueues a start command.
3. Control engine sets runtime transition to `starting`, enables plant, and conditionally sends the immediate setpoint only if dispatch sending is enabled for that plant.
4. Scheduler loop dispatches only while the per-plant dispatch-send gate is true.
5. Transition resolves to `running` on observed Modbus enable state (physical state is shown separately from dispatch send state).

### Pause/Resume Dispatch Sending
1. User toggles a plant Status card dispatch control (`Sending` / `Paused`).
2. Dashboard enqueues a dispatch enable/disable command intent.
3. Control engine updates the per-plant scheduler send gate without enabling/disabling the plant.
4. Status card reflects the new dispatch state and continues to show the last commanded/sent setpoint write info.

### Stop Plant Dispatch
1. User stops plant card.
2. Dashboard immediately shows a temporary `stopping` feedback state and enqueues a stop command.
3. Control engine executes safe-stop helper.
4. Helper returns `{threshold_reached, disable_ok}` and transition resolves accordingly.

### Edit Manual Override Schedule
1. User selects one of the four manual override series in the Manual Schedule editor.
2. User edits breakpoints (relative `HH:MM:SS` + setpoint) and/or loads a CSV with the same relative-row structure; non-empty schedules always show a terminal `end` row.
3. Dashboard auto-sanitizes row times (forward-only, minimum gap) and writes the selected series draft to shared state using the current start datetime.
4. Manual series is sanitized to local `current day + next day`; corresponding plot updates.
5. User toggles the series active/inactive to include/exclude it from merged dispatch.

### Switch Transport
1. User confirms switch in modal.
2. Dashboard enqueues a transport-switch command and closes the modal (optimistic selector feedback only).
3. Control engine safely stops both plants, applies transport mode, and clears switching flag.

### Record Session
1. User sets recording on for one plant.
2. Measurement agent writes null/session boundaries and measurement rows.
3. User stops recording; trailing boundary and forced flush complete session.

### Start All / Stop All
1. User requests fleet action from the Status top card.
2. Dashboard opens confirmation modal before execution.
3. On confirm, dashboard enqueues the fleet action command.
4. `Start All`: control engine enables recording for both plants, then plant start sequences execute (dispatch send gates are preserved; not auto-enabled).
5. `Stop All`: control engine safe-stops both plants, then recording is stopped for both plants.

### Browse Historical Plots
1. User opens `Plots` tab.
2. Dashboard scans `data/*.csv`, derives the global available measurement time range, and populates a timeline + range slider.
3. Range slider defaults to the full detected time span when the current slider value is invalid/stale (for example initial placeholder values before history bounds are loaded).
4. User adjusts the range slider; both plant plots update to the selected time window.
5. User optionally downloads cropped CSV or PNG for either plant.
