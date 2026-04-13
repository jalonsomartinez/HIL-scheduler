# System Patterns: HIL Scheduler

## Canonical Runtime Contracts
- Fixed plant IDs: `lib`, `vrfb`.
- Modbus transport contract:
  - client access is shared per endpoint (`backend`, `host`, `port`, `unit_id`) within process,
  - per-endpoint requests are lock-serialized to reduce session churn and plant contention.
- Modbus point-schema contract:
  - core read points remain required: `p_battery`, `q_battery`, `enable`, `p_poi`, `q_poi`, `v_poi`,
  - `soc` is optional,
  - setpoint writes must use exactly one family:
    - aggregate: `p_setpoint`, `q_setpoint`,
    - per-phase: `p_u_setpoint`, `p_v_setpoint`, `p_w_setpoint`, `q_u_setpoint`, `q_v_setpoint`, `q_w_setpoint`,
  - optional raw points include `trigger`, `start_command`, `stop_command`, `v_poi_write`.
- Authoritative selectors:
  - `transport_mode` (`local|remote`)
  - `posting_runtime.policy_enabled`
  - `api_connection_runtime.state`
- API credential selectors:
  - `api_password` runtime credential
  - `ISTENTORE_API_PASSWORD` startup config value from env `HIL_API_PASSWORD`
- API schedule selectors/maps:
  - `api_day_ahead_schedule_df_by_plant`
  - `api_mfrr_schedule_df_by_plant`
  - `api_schedule_df_by_plant` authoritative total consumed by scheduler
- Manual schedule selectors:
  - series keys: `lib_p`, `lib_q`, `vrfb_p`, `vrfb_q`
  - enabled flags in `manual_schedule_merge_enabled_by_key`
- Control gates:
  - `scheduler_running_by_plant`
  - `measurements_filename_by_plant`
- Local emulator seed maps:
  - `local_emulator_soc_seed_request_by_plant`
  - `local_emulator_soc_seed_result_by_plant`
- Dispatch write-status contract:
  - `dispatch_write_status_by_plant.<id>.last_scheduler_context` records `setpoint_mode`, compare source, and readback mismatch/success flags.
- Fetcher mFRR telemetry lives under `data_fetcher_status.mfrr_poll` with attempt/success/result/error/next fields.

## Authoritative Shared State
Primary contract is initialized in `build_initial_shared_data(config)`.
Important maps:
- `api_day_ahead_schedule_df_by_plant`
- `api_mfrr_schedule_df_by_plant`
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
- `data_fetcher_agent`:
  - fetches day-ahead schedule on existing cadence/gates,
  - polls mFRR on `ISTENTORE_MFRR_POLL_PERIOD_S`,
  - composes total schedule and publishes mFRR poll telemetry.
- `scheduler_agent`:
  - computes effective setpoints,
  - performs exact-word readback comparison when possible,
  - writes aggregate or per-phase targets when dispatch is enabled.
- `plant_agent`:
  - runs local Modbus plant emulation,
  - applies local SoC seed requests,
  - maintains internal SoC state even if no Modbus `soc` point exists.
- `measurement_agent`:
  - samples telemetry,
  - compresses and persists rows,
  - enriches rows with schedule-intent columns,
  - estimates SoC when real Modbus SoC is absent.
- `control_engine_agent`:
  - executes queued control commands and safe flows,
  - resets optional `trigger` before start,
  - uses the shared setpoint apply helper for initial writes and safe-stop zero writes.
- `settings_engine_agent`: executes API connect/disconnect, posting policy, and manual-series changes.
- `dashboard/agent.py` and `dashboard/public_agent.py`: render operator/public state without becoming additional sources of truth.

## Operational Patterns
- Safe stop contract:
  1. disable dispatch send gate,
  2. write zero P/Q,
  3. wait for decay threshold with fail-fast behavior on unreachable reads when configured,
  4. disable plant.
- Transport switch contract:
  1. modal confirm,
  2. safe-stop both plants,
  3. switch mode,
  4. invalidate stale observed/runtime state.
- Fleet action contract:
  - `Start All` remains transport-aware:
    - `local`: start/seed plants before enabling recording,
    - `remote`: preserve recording-first ordering.
  - `Stop All` safe-stops plants and stops recording.
- Shared SoC seed pattern:
  1. `runtime/soc_estimation.py` resolves startup SoC from latest persisted disk row when available.
  2. Fallback is `STARTUP_INITIAL_SOC_PU`, clamped to `[0.0, 1.0]`.
  3. Control engine uses the same resolver before local starts.
  4. Plant agent and measurement agent both initialize from the same resolved seed.
- Measurement SoC estimation pattern:
  1. each plant gets a `SocEstimator`,
  2. real `soc` samples call `sync(...)` and become authoritative,
  3. missing `soc` samples call `estimate_from_power(...)` using battery active power, capacity, and timestamp delta,
  4. out-of-order samples do not back-integrate state.
- Setpoint application pattern:
  1. scheduler/control build a write plan from endpoint schema,
  2. aggregate mode writes single `P` and `Q` points,
  3. per-phase mode writes equal thirds across U/V/W points,
  4. scheduler compares exact target words against register readback and skips writes when all words already match,
  5. if readback fails, scheduler falls back to last-command cache instead of blind churn,
  6. after successful writes, optional `trigger` is pulsed high then low,
  7. trigger failure is surfaced through dispatch write status/logging and leaves retry state armed.
- Start-command prepare pattern:
  - start flow resets optional `trigger` to `0` before command prepare,
  - failed trigger reset aborts the start transition before enable.
- API credential contract:
  1. startup may preload `api_password`,
  2. `Save Password` only updates runtime state,
  3. `Connect` uses stored password,
  4. `Disconnect` preserves the stored password.
- Schedule composition pattern:
  1. maintain day-ahead per plant,
  2. maintain mFRR per plant,
  3. recompute total as `total_p = day_ahead_p + mfrr_p`, `total_q = day_ahead_q`,
  4. missing mFRR timestamps contribute `0` through index union + numeric fill.
- VRFB mFRR alignment pattern:
  - VRFB mFRR is always an explicit zero frame on LIB mFRR timestamps.
- mFRR polling observability pattern:
  - attempt logs at DEBUG,
  - transition summaries at INFO,
  - unchanged steady-state summaries at DEBUG,
  - failures at ERROR.
- Modbus request-shaping pattern:
  - stable point sets use startup-built grouped reads,
  - grouped reads merge nearby holding-register addresses with bounded gap/block size.
- Dashboard navigation pattern:
  - dashboards are route/menu driven, not tab driven,
  - section visibility uses `page-section` / `page-section--active`,
  - public dashboard stays strictly read-only.
- Digital-twin mirror pattern:
  - `grid_map_digital_twin/` and `digital_twin_package/` must stay in sync,
  - `net_digital_twin.p` edits must be mirrored and backed up during model surgery.

## Time and Timestamp Conventions
- Runtime timestamps are timezone-aware in the configured timezone.
- Schedule and measurement series are normalized before plotting and selection.
- Status plots use a local current-day + next-day window.
- Historical plots use epoch-ms range sliders over indexed CSV availability.
- API-tab mFRR telemetry uses configured local timezone formatting.

## Locking Discipline
- `shared_data["lock"]` protects shared mutable runtime structures.
- Dashboard callbacks snapshot while locked and render outside the lock.
- Queue lifecycle and engine status publication use shared runtime helpers in `runtime/`.
