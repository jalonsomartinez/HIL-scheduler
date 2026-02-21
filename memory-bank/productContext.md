# Product Context: HIL Scheduler

## Why This Exists
The system closes the operational gap between market/control schedules and plant-level setpoint execution for two battery assets. It allows hardware-in-the-loop operation with equivalent control flows in local emulation and remote transport.

## Primary Users
1. Operators running live tests and needing safe controls.
2. Engineers validating schedule logic, telemetry, and API integration.

## Core User Outcomes
1. Start and stop dispatch safely per plant.
2. Record clean per-plant measurement sessions without mixed datasets.
3. Switch schedule source or transport mode with guarded transitions.
4. See real-time status for schedule freshness and API posting health.
5. Trigger high-impact fleet-wide start/stop actions with explicit confirmation.
6. Run API-mode read-only tests by disabling measurement posting at runtime.

## Product Behavior
### Scheduling
- Manual schedules are prepared per plant from random generation or CSV upload.
- API schedules are fetched per plant from one market response.
- A global source selector chooses whether scheduler dispatch reads manual or API maps.

### Dispatch
- Dispatch enable is per plant through `scheduler_running_by_plant`.
- Start enables plant and applies immediate setpoint selection.
- Stop executes safe-stop flow (gate off, zero setpoints, decay wait, disable).

### Recording
- Recording enable is per plant through `measurements_filename_by_plant`.
- Measurement agent owns boundary insertion, buffering, tolerance-based compression, and flushes.
- Rows are routed by row timestamp to daily per-plant files.

### Observability
- API tab shows fetch status (today/tomorrow) plus measurement posting telemetry.
- API tab includes a runtime posting toggle (`Enabled`/`Disabled`) for session-scoped read-only testing.
- Status tab inline API summary surfaces today/tomorrow fetched-point counts for both plants.
- Logs tab exposes a live `Today` view (tail of the current date log file) and selectable historical log files.

## UX Intent
1. Clear separation between dispatch control and recording control.
2. Explicit transition states (`starting`, `running`, `stopping`, `stopped`, `unknown`).
3. Safe confirmation flows before global source/transport changes.
4. Stable plot interactions during periodic refresh.

## Critical Workflows
### Start Plant Dispatch
1. User starts plant card.
2. Dashboard sets transition to `starting`, enables plant, and sends immediate setpoint.
3. Scheduler loop continues dispatch while the plant gate is true.
4. Transition resolves to `running` on observed plant state.

### Stop Plant Dispatch
1. User stops plant card.
2. Dashboard executes safe-stop helper.
3. Helper returns `{threshold_reached, disable_ok}` and transition resolves accordingly.

### Switch Global Source or Transport
1. User confirms switch in modal.
2. Dashboard safely stops both plants.
3. Dashboard updates global selector and clears switching flag.

### Record Session
1. User sets recording on for one plant.
2. Measurement agent writes null/session boundaries and measurement rows.
3. User stops recording; trailing boundary and forced flush complete session.

### Start All / Stop All
1. User requests fleet action from the Status top card.
2. Dashboard opens confirmation modal before execution.
3. `Start All`: recording is enabled for both plants, then plant start sequences execute.
4. `Stop All`: safe-stop runs for both plants, then recording is stopped for both plants.
