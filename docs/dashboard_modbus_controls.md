# Dashboard Control/Modbus Boundary

This document summarizes the current control boundary after the first dashboard hardening pass.

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
- cached plant observed state publication (`enable`, `p_battery`, `q_battery`).

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

### API tab actions
- `Set Password`: stores runtime API password in shared state.
- `Disconnect`: clears runtime API password.
- `Measurement Posting Enabled/Disabled`: toggles runtime gate `measurement_posting_enabled`.

### Manual schedule actions
- `Generate Random`: build random schedule for selected plant.
- `Clear Plant Schedule`: clear manual schedule map entry for selected plant.
- CSV upload: parse + normalize + store uploaded schedule for selected plant.

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
- Remaining UI-owned state mutations (manual schedule editor and API settings/password actions) are follow-up scope.
