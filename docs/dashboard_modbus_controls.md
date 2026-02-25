# Dashboard Control/Modbus Boundary

This document summarizes the current control/settings boundary after the second dashboard hardening pass.

## Current architecture (as implemented)

The dashboard (`dashboard_agent.py`) is now a thin UI for control paths:
- renders state from shared runtime data,
- enqueues control intents/commands,
- does not execute plant control flows or direct Modbus I/O for start/stop/transport/status.

A dedicated runtime engine (`control_engine_agent.py`) now owns:
- command queue consumption (FIFO),
- plant/fleet/transport control execution,
- safe-stop sequencing,
- Modbus reads/writes for those flows,
- cached plant observed state publication (`enable`, `p_battery`, `q_battery`) plus Modbus read/connectivity/freshness error metadata,
- control-engine health/queue status publication for dashboard display (queue depth, active command, recent failures, last loop error/last finished command).

A dedicated settings engine (`settings_engine_agent.py`) now owns:
- manual schedule activation/inactivation/update commands (per series),
- API connect/disconnect commands (password storage separated from connection state),
- posting policy enable/disable commands,
- server-owned transition/runtime state for manual/API/posting UI rendering.

## Dashboard behavior (control paths)

### Dashboard actions now enqueue commands

The dashboard maps UI actions to command intents and enqueues them into shared state:
- `plant.start`
- `plant.stop`
- `plant.record_start`
- `plant.record_stop`
- `fleet.start_all`
- `fleet.stop_all`
- `transport.switch`

The dashboard callback returns immediately after enqueueing. Execution happens in `control_engine_agent.py`.

## Modbus reads/writes ownership

### Dashboard-owned Modbus I/O (control/status paths)
- None (for the hardened control paths in this pass).

### Control-engine-owned Modbus I/O
1. **Plant start**
   - `enable=1`
   - initial `p_setpoint` / `q_setpoint`
2. **Plant stop / Stop All / Transport switch**
   - safe-stop flow:
     - zero setpoints,
     - decay polling (`p_battery`, `q_battery`),
     - disable (`enable=0`)
3. **Observed-state cache polling**
   - periodic best-effort reads for:
     - `enable`
     - `p_battery`
     - `q_battery`
   - results published into `shared_data["plant_observed_state_by_plant"]`

## Status callback behavior

`update_status_and_graphs` in `dashboard_agent.py` no longer reads Modbus directly.

It now reads `shared_data["plant_observed_state_by_plant"]` and treats `enable_state` as unknown when the cached state is marked stale.

The Status tab also reads server-published:
- `shared_data["control_engine_status"]` for control-engine/queue health summary,
- extended observed-state metadata (`read_status`, `last_error`, `consecutive_failures`) for per-plant Modbus link diagnostics.

## Status tab health surfacing (current)

### Top-card runtime health
- `Control Engine` summary line shows:
  - alive/stopped state,
  - queue depth,
  - active command (id/kind/runtime age),
  - last finished command,
  - last loop error (if any).
- `Command Queue` summary line shows:
  - queued count,
  - running count,
  - recent failed/rejected count,
  - `Backlog: HIGH` hint for elevated queue depth (presentation-only threshold).

### Per-plant Modbus health details
- Existing per-plant status sections now include:
  - Modbus link/read condition (`OK`, `CONNECT_FAILED`, `READ_ERROR`, `UNKNOWN`),
  - observed-state freshness age / stale marker,
  - consecutive failure count,
  - last error message (if available).

### API tab actions
- `Connect`: enqueues `api.connect` (uses input password if provided, otherwise stored password).
- `Disconnect`: enqueues `api.disconnect` (intentionally disconnects without clearing stored password).
- `Measurement Posting Enabled/Disabled`: enqueue `posting.enable` / `posting.disable` settings commands.

Dashboard renders server-owned API/posting state and uses short optimistic transition feedback on buttons (`Connecting...`, `Disconnecting...`, `Enabling...`, `Disabling...`).

### Manual schedule actions
- Editor load/save/edit remains dashboard-owned (draft series data).
- Per-series `Activate` / `Inactivate` / `Update` now enqueue settings commands:
  - `manual.activate`
  - `manual.inactivate`
  - `manual.update`
- Scheduler continues dispatching from server-applied manual series (`manual_schedule_series_df_by_key`) and merge flags; dashboard drafts are separate.

## Recommended architecture boundary (target)

This hardening pass implements the desired model for control paths:

- UI should be limited to:
  - rendering current state,
  - sending user intents/commands.
- Runtime engine should own:
  - all control-flow execution,
  - all Modbus reads/writes,
  - transitions/safe-stop sequencing,
  - authoritative state publication.

### Notes on scheduler-agent role

- `scheduler_agent.py` is the periodic setpoint dispatch engine (it applies schedule setpoints when per-plant scheduler gates are enabled).
- `control_engine_agent.py` now handles non-periodic operator control execution (start/stop/transport/recording control) and plant observed-state polling.
- Remaining UI-owned mutations after this pass are primarily editor draft manipulation and API password field input (the resulting state transitions are settings-engine commands).
