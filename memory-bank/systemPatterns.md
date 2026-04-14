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
- `reactive_control_mode_by_plant`
- `reactive_control_mode_runtime_by_plant`
- `grid_map_runtime`

## Agent Responsibilities
- `scheduler_agent`:
  - resolves dispatch bundle from API base plus manual per-signal overrides,
  - selects classic `Q` mode or voltage-regulation mode from `reactive_control_mode_by_plant`,
  - resolves `v_setpoint_pu` from manual voltage first, then digital twin summary, then `1.0 pu`,
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
  3. Manual voltage override does not change `P`; it only provides a higher-priority `v_setpoint_pu` source.
  4. If there is no current manual voltage value, runtime falls through to digital twin voltage reference for eligible plants.
  5. Final resolved `v_setpoint_pu` is clamped to `[0.9, 1.1]`.
- Reactive dispatch pattern:
  1. selected reactive mode `1` -> classic reactive mode,
  2. if `q_control_mode` exists, write mode `1`,
  3. dispatch resolved `Q` setpoint.
- Voltage-regulation pattern:
  1. selected reactive mode `3` -> voltage mode,
  2. require active endpoint `q_control_mode`,
  3. resolve `v_setpoint_pu` from manual voltage, else digital twin, else `1.0 pu`,
  4. digital twin formula is `battery_voltage_pu + 1.0 - (max_voltage_pu + min_voltage_pu) / 2`,
  5. clamp the resolved voltage reference to `[0.9, 1.1]`,
  6. compute `v_measured_pu = v_poi_kV / poi_voltage_kv`,
  7. compute `q_cmd = ((v_setpoint_pu - v_measured_pu) / droop_pu) * q_max_kvar`,
  8. clamp to plant `q_min_kvar/q_max_kvar`,
  9. write mode `3` plus setpoints,
  10. fail explicitly if `q_control_mode` is missing or `v_poi` cannot be read.
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
