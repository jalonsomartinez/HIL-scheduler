# System Patterns: HIL Scheduler

## Canonical Runtime Contracts
- Fixed plant IDs: `lib`, `vrfb`.
- Authoritative selectors:
  - `transport_mode` (`local|remote`)
  - `posting_runtime.policy_enabled`
  - `api_connection_runtime.state`
- Manual schedules:
  - series keys: `lib_p`, `lib_q`, `vrfb_p`, `vrfb_q`
  - enabled flags in `manual_schedule_merge_enabled_by_key`
  - effective dispatch is API base overwritten by enabled manual series.
- Control gates per plant:
  - `scheduler_running_by_plant` (dispatch send gate)
  - `measurements_filename_by_plant` (recording on/off)
- Command queues:
  - `control_command_queue` for plant/transport/fleet/record/dispatch actions
  - `settings_command_queue` for API/manual settings operations.

## Authoritative Shared State
Primary contract is initialized in `build_initial_shared_data(config)`.
Key maps:
- `api_schedule_df_by_plant`
- `manual_schedule_series_df_by_key`
- `manual_schedule_draft_series_df_by_key`
- `current_file_df_by_plant`
- `plant_observed_state_by_plant`
- `plant_operating_state_by_plant`
- `dispatch_write_status_by_plant`
- `control_engine_status`
- `settings_engine_status`

## Agent Responsibilities
- `data_fetcher_agent`: fetches API schedules and publishes fetch status.
- `scheduler_agent`: computes effective setpoints and writes when dispatch sending is enabled.
- `plant_agent`: local Modbus plant emulation.
- `measurement_agent`: sampling, compression, file writes, API posting queue.
- `control_engine_agent`: executes queued control commands and safe flows.
- `settings_engine_agent`: executes API connect/disconnect, posting policy, manual series activation/update.
- `dashboard/agent.py`: private operator UI callbacks/intents and plots.
- `dashboard/public_agent.py`: public read-only UI and plots.

## Operational Patterns
- Safe stop contract:
  1. disable dispatch send gate,
  2. write zero P/Q,
  3. wait for decay threshold (with fail-fast fallback on unreachable reads),
  4. disable plant.
- Transport switch contract:
  1. modal confirm,
  2. safe-stop both plants,
  3. switch mode,
  4. invalidate stale physical/observed runtime state.
- Fleet actions:
  - `Start All` enables recording + dispatch gates then starts plants.
  - `Stop All` safe-stops plants and stops recording.
- Public dashboard is strictly read-only: no enqueue helpers and no write-side actions.

## Time and Timestamp Conventions
- Runtime timestamps are timezone-aware in configured timezone.
- Schedule and measurement series are normalized before plotting/selection.
- Status plots use a local current-day + next-day window.
- Historical plots use epoch-ms range sliders over indexed CSV availability.

## Locking Discipline
- `shared_data["lock"]` protects all shared mutable runtime structures.
- Dashboard callbacks copy snapshots while locked, then render outside lock.
- Queue lifecycle and engine status updates use shared runtime helpers (`runtime/command_runtime.py`, `runtime/engine_command_cycle_runtime.py`, `runtime/engine_status_runtime.py`).
