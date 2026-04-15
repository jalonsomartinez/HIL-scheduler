# System Patterns: HIL Scheduler

## Canonical Runtime Contracts
- Fixed plant IDs: `lib`, `vrfb`.
- Modbus point-schema contract:
  - required read points: `p_battery`, `q_battery`, `enable`, `p_poi`, `q_poi`, `v_poi`,
  - optional `soc`,
  - setpoint writes use exactly one family:
    - aggregate: `p_setpoint`, `q_setpoint`,
    - per-phase: `p_u_setpoint`, `p_v_setpoint`, `p_w_setpoint`, `q_u_setpoint`, `q_v_setpoint`, `q_w_setpoint`,
  - optional raw points include `trigger`, `start_command`, `stop_command`, `q_control_mode`,
  - voltage-mode-capable endpoints also expose `v_setpoint`.
- Grid-map voltage-write contract:
  - optional standalone endpoint lives at `grid_map.voltage_write_modbus.{local,remote}`,
  - each configured transport endpoint supports exactly one point: `v_poi_write`,
  - active transport mode selects which endpoint is used,
  - grid-map writes happen once per cycle through this standalone endpoint,
  - digital-twin simulation `P/Q` inputs still come from LIB measured power, not from the standalone voltage-write endpoint.
- Manual schedule selectors:
  - series keys: `lib_p`, `lib_q`, `lib_v`, `vrfb_p`, `vrfb_q`, `vrfb_v`,
  - enabled flags live in `manual_schedule_merge_enabled_by_key`.
- Dispatch write-status contract:
  - `dispatch_write_status_by_plant.<id>.last_scheduler_context` includes reactive mode, voltage-mode flag, write quantity mode (`pq` or `pv`), and resolved `v_setpoint_pu`.
- Digital-twin history contract:
  - plant measurement rows stay plant-specific and do not persist shared `grid_map_*` summary metrics,
  - shared Grid Map summary metrics plus seven voltage-bucket node counts are recorded into dedicated `data/YYYYMMDD_twin.csv` files for the with-battery scenario,
  - the no-battery scenario is recorded into matching `data/YYYYMMDD_twin_nobat.csv` files,
  - shared historical plots read only from those twin file families instead of coalescing duplicated plant data.

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
  - writes optional `q_control_mode` before setpoint apply when configured,
  - writes plant `v_setpoint` directly when voltage mode is active.
- `measurement_agent`:
  - samples telemetry,
  - enriches plant rows with schedule-intent columns and `v_setpoint_pu`,
  - records shared digital-twin summary rows into the singleton twin-history files (`twin`, `twin_nobat`) whenever either plant recording session is active,
  - estimates SoC when real Modbus SoC is absent.
- `control_engine_agent`:
  - uses the same dispatch-bundle logic as scheduler for start-time initial setpoints,
  - keeps start/safe-stop flows aligned with runtime reactive-control behavior.
- `settings_engine_agent`:
  - activates, updates, and inactivates manual `P`, `Q`, and voltage series through one common path.

## Operational Patterns
- Grid-map runtime pattern:
  1. select LIB battery `P/Q` from fresh observed state when available,
  2. otherwise fall back to latest LIB measurement cache row,
  3. run the digital twin once with those LIB power inputs and once with battery `P=0`, `Q=0`,
  4. publish both scenario payloads under `grid_map_runtime.scenario_results`,
  5. keep top-level runtime summary/dynamic payload aligned to the with-battery scenario for backward compatibility,
  6. compute summary metrics including battery voltage, min/max voltage, max line loading, overloaded-line count, and detailed voltage-bucket node counts for each scenario,
  7. if `grid_map.voltage_write_modbus.<active transport>` is configured, write `v_poi_write` once through that endpoint using only the with-battery result,
  8. rely on the shared Modbus client pool to reuse the underlying TCP transport automatically when `host + port` matches another runtime client.
- Historical plots pattern:
  1. scan per-plant measurement files for the selected range,
  2. scan the shared `twin` and `twin_nobat` history file families alongside LIB and VRFB,
  3. load cropped LIB and VRFB measurement frames independently,
  4. load cropped twin-history frames independently for both scenarios,
  5. build per-plant figures directly from each plant frame,
  6. build three shared digital-twin figures from twin files only:
    - with battery,
    - no battery,
    - signed impact (`with_battery - without_battery`),
  7. render explicit empty-state figures when the selected range has no populated comparable twin rows.
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
  2. require active endpoint `q_control_mode` and `v_setpoint`,
  3. resolve `v_setpoint_pu` from manual voltage, else digital twin, else `1.0 pu`,
  4. digital twin formula is `battery_voltage_pu + 1.0 - (max_voltage_pu + min_voltage_pu) / 2`,
  5. clamp the resolved voltage reference to `[0.9, 1.1]`,
  6. convert `v_setpoint_pu` to plant physical voltage using configured `poi_voltage_kv`,
  7. write mode `3`, `P`, and plant `v_setpoint`,
  8. skip `Q` setpoint writes in voltage mode,
  9. fail explicitly if `q_control_mode` or `v_setpoint` is missing.
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
