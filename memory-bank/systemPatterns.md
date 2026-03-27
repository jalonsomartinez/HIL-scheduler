# System Patterns: HIL Scheduler

## Canonical Runtime Contracts
- Fixed plant IDs: `lib`, `vrfb`.
- Modbus transport contract:
  - runtime client access is shared per endpoint (`backend`, `host`, `port`, `unit_id`) within process,
  - per-endpoint requests are lock-serialized to reduce session churn/contention on plant servers.
- Authoritative selectors:
  - `transport_mode` (`local|remote`)
  - `posting_runtime.policy_enabled`
  - `api_connection_runtime.state`
- API credential selectors:
  - `api_password` (runtime stored credential used by connect/fetch/posting paths)
  - `ISTENTORE_API_PASSWORD` (startup config key loaded from env `HIL_API_PASSWORD`)
- API schedule selectors/maps:
  - `api_day_ahead_schedule_df_by_plant` (market id 4 component),
  - `api_mfrr_schedule_df_by_plant` (market id 3 component),
  - `api_schedule_df_by_plant` (authoritative total consumed by scheduler).
- Manual schedules:
  - series keys: `lib_p`, `lib_q`, `vrfb_p`, `vrfb_q`
  - enabled flags in `manual_schedule_merge_enabled_by_key`
  - effective dispatch is API base overwritten by enabled manual series.
- Control gates per plant:
  - `scheduler_running_by_plant` (dispatch send gate)
  - `measurements_filename_by_plant` (recording on/off)
- Local emulator seed maps:
  - `local_emulator_soc_seed_request_by_plant`
  - `local_emulator_soc_seed_result_by_plant`
- Command queues:
  - `control_command_queue` for plant/transport/fleet/record/dispatch actions
  - `settings_command_queue` for API/manual settings operations, including `api.password.set`.
- Fetcher mFRR telemetry contract under `data_fetcher_status.mfrr_poll`:
  - `last_attempt_at`, `last_success_at`, `last_result`, `last_error`,
  - `last_points_lib`, `next_scheduled_at`, `poll_period_s`.

## Authoritative Shared State
Primary contract is initialized in `build_initial_shared_data(config)`.
Key maps:
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
  - polls mFRR on dedicated cadence (`ISTENTORE_MFRR_POLL_PERIOD_S`),
  - composes total schedule and publishes mFRR poll telemetry.
- `scheduler_agent`: computes effective setpoints and writes when dispatch sending is enabled.
- `plant_agent`: local Modbus plant emulation.
- `measurement_agent`: sampling, compression, file writes, API posting queue, and as-of schedule-intent enrichment.
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
  - `Start All` is transport-aware:
    - `local`: enables dispatch gates, starts/seeds plants, then enables recording for both plants.
    - `remote`: keeps recording-first ordering, then starts plants.
  - `Stop All` safe-stops plants and stops recording.
- Local SoC restore patterns:
  - Startup local emulator initialization restores per-plant SoC from latest persisted non-null on-disk `soc_pu` when available, else falls back to `STARTUP_INITIAL_SOC_PU`.
  - Local per-plant start still performs control-engine-to-plant-agent SoC seed handshake before enable.
  - SoC seed values are clamped to `[0.0, 1.0]` before applying to emulator state/registers.
- API credential and connection contract:
  1. startup may preload `api_password` from env-derived config,
  2. dashboard `Save Password` enqueues `api.password.set` (no connect side effect),
  3. dashboard `Connect` enqueues `api.connect` and uses stored password,
  4. `Disconnect` updates runtime state but preserves stored password.
- Schedule composition pattern:
  1. day-ahead component map is maintained per plant,
  2. mFRR component map is maintained per plant,
  3. total component is recomputed as `total_p = day_ahead_p + mfrr_p`, `total_q = day_ahead_q`,
  4. missing mFRR timestamps contribute `0` through index union + numeric fill.
- VRFB mFRR alignment pattern:
  - VRFB mFRR is always explicit zero frame on LIB mFRR timestamps (authoritative mFRR window), including empty-frame behavior when LIB mFRR is empty.
- mFRR polling observability pattern:
  - per-poll mFRR fetch-attempt log is DEBUG,
  - poll-result summary logs are INFO on result/point-count transitions and DEBUG when unchanged,
  - polling errors remain ERROR.
- Modbus request-shaping pattern:
  - stable point sets (measurement and control observed-state) use startup/setup-built grouped reads,
  - grouped reads merge nearby holding-register addresses with bounded gap/block size,
  - scheduler write/readback loop remains functionally unchanged unless explicitly extended.
- Public dashboard is strictly read-only: no enqueue helpers and no write-side actions.
- Public basic-auth contract ensures Flask session secret key is set before auth middleware to avoid session warnings.
- Dashboard navigation pattern:
  - tab components were removed from both dashboards,
  - section visibility is route-driven (`page-section` / `page-section--active`),
  - menu drawer toggle state is callback-controlled and auto-closes on non-toggle triggers.

## Time and Timestamp Conventions
- Runtime timestamps are timezone-aware in configured timezone.
- Schedule and measurement series are normalized before plotting/selection.
- Status plots use a local current-day + next-day window.
- Historical plots use epoch-ms range sliders over indexed CSV availability.
- mFRR telemetry timestamps displayed in API tab follow configured local timezone formatting.

## Locking Discipline
- `shared_data["lock"]` protects all shared mutable runtime structures.
- Dashboard callbacks copy snapshots while locked, then render outside lock.
- Queue lifecycle and engine status updates use shared runtime helpers (`runtime/command_runtime.py`, `runtime/engine_command_cycle_runtime.py`, `runtime/engine_status_runtime.py`).
