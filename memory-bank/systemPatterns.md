# System Patterns: HIL Scheduler

## Canonical Runtime Contracts
- Fixed plant IDs: `lib`, `vrfb`.
- Modbus point-schema contract:
  - required read points: `p_battery`, `q_battery`, `enable`, `p_poi`, `q_poi`, `v_poi`,
  - optional `soc`,
  - setpoint writes use exactly one family:
    - aggregate: `p_setpoint`, `q_setpoint`,
    - per-phase: `p_u_setpoint`, `p_v_setpoint`, `p_w_setpoint`, `q_u_setpoint`, `q_v_setpoint`, `q_w_setpoint`,
  - optional raw points include `trigger`, `start_command`, `stop_command`, `v_poi_write`, `q_control_mode`.
- Manual schedule selectors:
  - series keys: `lib_p`, `lib_q`, `lib_v`, `vrfb_p`, `vrfb_q`, `vrfb_v`,
  - enabled flags live in `manual_schedule_merge_enabled_by_key`.
- Dispatch write-status contract:
  - `dispatch_write_status_by_plant.<id>.last_scheduler_context` now includes reactive mode, voltage-mode flag, resolved `v_setpoint_pu`, and measured `v_poi_pu` when voltage mode is used.

## Authoritative Shared State
Primary contract is initialized in `build_initial_shared_data(config)`.
Important maps:
- `api_day_ahead_schedule_df_by_plant`
- `api_mfrr_schedule_df_by_plant`
- `api_schedule_df_by_plant`
- `manual_schedule_series_df_by_key`
- `manual_schedule_draft_series_df_by_key`
- `manual_series_runtime_state_by_key`
- `current_file_df_by_plant`
- `dispatch_write_status_by_plant`

## Agent Responsibilities
- `scheduler_agent`:
  - resolves dispatch bundle from API base plus manual per-signal overrides,
  - selects classic `Q` mode or voltage-regulation mode from the manual voltage channel state,
  - computes `Q` from measured voltage and droop when voltage mode is active,
  - writes optional `q_control_mode` before setpoint apply when configured.
- `measurement_agent`:
  - samples telemetry,
  - enriches rows with schedule-intent columns and `v_setpoint_pu`,
  - estimates SoC when real Modbus SoC is absent.
- `control_engine_agent`:
  - uses the same dispatch-bundle logic as scheduler for start-time initial setpoints,
  - keeps start/safe-stop flows aligned with runtime reactive-control behavior.
- `settings_engine_agent`:
  - activates, updates, and inactivates manual `P`, `Q`, and voltage series through one common path.

## Operational Patterns
- Manual override pattern:
  1. API base setpoint is resolved first with staleness handling.
  2. Manual `P` and `Q` overrides replace only their signals when active and in-range.
  3. Manual voltage override does not change `P`; it selects voltage mode and provides `v_setpoint_pu`.
  4. Missing manual voltage value resolves to `1.0 pu`.
- Reactive dispatch pattern:
  1. inactive voltage channel -> classic reactive mode,
  2. if `q_control_mode` exists, write mode `1`,
  3. dispatch resolved `Q` setpoint.
- Voltage-regulation pattern:
  1. active manual voltage channel -> voltage mode,
  2. require active endpoint `q_control_mode`,
  3. compute `v_measured_pu = v_poi_kV / poi_voltage_kv`,
  4. compute `q_cmd = ((v_setpoint_pu - v_measured_pu) / droop_pu) * q_max_kvar`,
  5. clamp to plant `q_min_kvar/q_max_kvar`,
  6. write mode `3` plus setpoints,
  7. fail explicitly if `q_control_mode` is missing or `v_poi` cannot be read.
- Setpoint application pattern:
  1. scheduler/control build a write plan from endpoint schema,
  2. aggregate mode writes single `P` and `Q`,
  3. per-phase mode writes equal thirds,
  4. scheduler compares exact target words against readback and skips redundant writes,
  5. optional `trigger` pulses after successful writes.

## Time and Timestamp Conventions
- Runtime timestamps are timezone-aware in the configured timezone.
- Schedule and measurement series are normalized before plotting and selection.
- Status and manual-schedule windows remain current-day + next-day oriented.

## Locking Discipline
- `shared_data["lock"]` protects shared mutable runtime structures.
- Dashboard callbacks snapshot while locked and render outside the lock.
- Queue lifecycle and engine status publication use shared runtime helpers in `runtime/`.
