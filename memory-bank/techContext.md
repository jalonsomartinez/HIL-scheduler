# Technical Context: HIL Scheduler

## Technology Stack
- Language: Python 3.
- UI: Dash + Plotly.
- UI assets: tokenized CSS in `assets/custom.css` with local static brand assets under `assets/brand/`.
- Data processing: pandas, numpy.
- Modbus: pyModbusTCP client/server.
- Config: YAML via PyYAML.
- HTTP API integration: requests.

## Repository Runtime Modules
- `hil_scheduler.py`: director, shared state initialization, thread startup/shutdown.
- `control_engine_agent.py`: serial command execution engine for dashboard-issued control intents; owns control-path Modbus I/O and cached plant observed-state publication.
- `dispatch_write_runtime.py`: shared helpers for per-plant dispatch write status publication (`last attempt/success`, source/status/error) and dispatch-send enabled mirrors.
- `command_runtime.py`: generic shared-state command queue/lifecycle bookkeeping helpers used by control/settings wrappers.
- `control_command_runtime.py`: shared-state command queue/lifecycle bookkeeping helpers (ID allocation, queued/running/terminal status updates, bounded history).
- `settings_engine_agent.py`: serial settings command execution engine for manual activation/update/inactivation, API connect/disconnect, and posting policy commands.
- `settings_command_runtime.py`: settings-engine command queue/lifecycle bookkeeping wrappers (same shared helper pattern as control commands).
- `api_runtime_state.py`: shared API connection runtime-state publisher/recompute helpers (connect intent/state + fetch/posting sub-health -> authoritative API state).
- `engine_status_runtime.py`: shared engine queue/command-status summary publisher helpers reused by control and settings engines.
- `engine_command_cycle_runtime.py`: shared command lifecycle execution bookkeeping helper (`running`/`finished`/exception->status publication) reused by control and settings engines.
- `dashboard_command_intents.py`: pure dashboard trigger->command intent mapping helpers for UI callbacks.
- `dashboard_settings_intents.py`: pure dashboard trigger->settings-command mapping helpers (manual/API/posting).
- `dashboard_settings_ui_state.py`: pure UI transition/button-state helpers for manual/API/posting commanded resources.
- `dashboard_control_health.py`: pure Status-tab health formatting helpers for control-engine queue/runtime summaries and per-plant Modbus diagnostics.
- `dashboard_control_health.py`: pure Status-tab health formatting helpers for control-engine queue/runtime summaries, per-plant Modbus diagnostics, and dispatch-write status lines (including compact scheduler readback hints when scheduler telemetry is available).
- `config_loader.py`: validates/normalizes YAML into runtime dict.
- `dashboard_agent.py`: UI layout and callbacks; enqueues control + settings intents (including per-plant dispatch pause/resume), renders status from shared state/cached plant observations, applies short click-feedback transition overlays, and keeps manual editor drafts dashboard-owned.
- `dashboard_layout.py`: Dash layout builder; Status plant cards include independent per-plant dispatch toggles (`Sending` / `Paused`) in addition to start/stop and recording controls.
- `manual_schedule_manager.py`: manual override series metadata, editor breakpoint row conversions/auto-sanitization, relative CSV load/save parsing, terminal `end` row <-> stored duplicate-row encoding, and manual-series rebuild/sanitization helpers.
- `dashboard_history.py`: historical plots helper utilities (file scan/index, slider range helpers, CSV crop/export serialization).
- `dashboard_plotting.py`: shared Plotly figure/theme helpers for status and historical plant plots, including optional x-window cropping, Status-tab current-time indicator lines, historical setpoint fallback from measurement rows, and optional voltage y-range padding override.
- `dashboard_control.py`: safe-stop/transport-switch control-flow helpers reused by control engine.
- `assets/custom.css`: dashboard design tokens, responsive rules, control/tab/modal/log styling.
- `assets/brand/fonts/*`: locally served dashboard fonts (DM Sans files + OFL license).
- `data_fetcher_agent.py`: day-ahead API polling and status updates.
- `scheduler_agent.py`: per-plant setpoint dispatch plus dispatch-write status publication/retry-aware dedupe behavior, with readback reconciliation against plant `p_setpoint`/`q_setpoint` registers using register-exact compare and cache fallback on read failure.
- `plant_agent.py`: local dual-server plant emulation.
- `measurement_agent.py`: sampling, recording, cache, API posting queue.
- `measurement_storage.py`: measurement normalization, CSV read/write helpers, latest persisted per-plant SoC lookup helper, and row-similarity primitives for compression.
- `istentore_api.py`: API auth, schedule fetch, measurement post, and bounded token re-auth retry on `401`/`403`.
- `time_utils.py`: timezone normalization and serialization helpers.
- `logger_config.py`: console/file/session logging setup.

## Configuration Schema (Current)
`config.yaml` canonical keys:
- `general.log_level`
- `time.timezone`
- `schedule.source_csv`, `schedule.duration_h`, `schedule.default_resolution_min`
- `startup.schedule_source`, `startup.transport_mode`, `startup.initial_soc_pu`
- `timing.*_period_s`
- `istentore_api.*`
- `recording.compression.*`
- `plants.lib.*` and `plants.vrfb.*`

Notes:
- `schedule.*` is parsed by `config_loader.py`; active scheduler dispatch uses in-memory per-plant schedule maps and does not consume these keys directly.
- `startup.schedule_source` is still parsed for compatibility, but dispatch no longer uses source switching (merged API base + manual overrides is always active).
- `istentore_api.tomorrow_poll_start_time` is the canonical next-day polling gate key for API day-ahead fetches; legacy `istentore_api.poll_start_time` is intentionally rejected (breaking rename, no alias).
- `recording.compression.*` is parsed and applied by `measurement_agent.py` for tolerance-based in-memory row compaction, configurable keep-gap retention (`max_kept_gap_s`), and periodic flush tail retention.
- Legacy flat alias keys from `config_loader.py` are disabled by default and are only emitted when `HIL_ENABLE_LEGACY_CONFIG_ALIASES=1`.

Per-plant config includes:
- `name`
- `model.capacity_kwh`, `model.power_limits`, `model.poi_voltage_kv`
- `modbus.local` and `modbus.remote` endpoints with required connection ordering (`byte_order`, `word_order`)
- endpoint `points` map entries (holding-register-only) with explicit `address`, `format`, `access`, `unit`, `eng_per_count`
- canonical point keys include `p_setpoint`, `q_setpoint`, `p_battery`, `q_battery`, `enable`, `soc`, `p_poi`, `q_poi`, `v_poi` (optional command points such as `start_command` / `stop_command` are preserved if present)
- `measurement_series` IDs for `soc`, `p`, `q`, `v`

## Runtime Contracts Exposed by Config Loader
- `PLANTS`: normalized per-plant map.
- `PLANTS[*].modbus.*` exposes endpoint `host`, `port`, `byte_order`, `word_order`, and normalized `points`.
- `PLANT_IDS`: `("lib", "vrfb")`.
- `STARTUP_SCHEDULE_SOURCE`, `STARTUP_TRANSPORT_MODE`.
- `STARTUP_SCHEDULE_SOURCE` remains compatibility metadata (dispatch no longer branches on it).
- Timing/posting/settings flattened for agents (for example `SCHEDULER_PERIOD_S`, `ISTENTORE_*`).
- API fetcher next-day poll gate is exposed as normalized `ISTENTORE_TOMORROW_POLL_START_TIME` (`HH:MM`).
- Recording compression settings are flattened for agents, including `MEASUREMENT_COMPRESSION_ENABLED`, `MEASUREMENT_COMPRESSION_TOLERANCES`, and `MEASUREMENT_COMPRESSION_MAX_KEPT_GAP_S`.

## Modbus and Unit Conventions
- Modbus runtime contract is schema-driven per endpoint/point (`config.yaml`) and uses holding registers only.
- Endpoint-level ordering is required (`byte_order`, `word_order`) and shared by all points on that endpoint (no loader defaults).
- Shared codec implementation lives in `modbus_codec.py`; unit conversion between Modbus engineering values and internal runtime units is handled by `modbus_units.py`.
- Runtime Modbus read/write helpers now apply both:
  - point binary codec (`format`, `eng_per_count`)
  - point unit conversion (`unit`) to/from internal units
- Internal runtime units:
  - SoC: `pu`
  - active power: `kW`
  - reactive power: `kvar`
  - voltage: `kV` (measurement field `v_poi_kV`)
- Current configured point formats/scales:
  - `p_*` / `q_*` power and setpoint points: `int16`, `eng_per_count=0.1` (`kW` / `kvar`)
  - `soc`: `uint16`, `eng_per_count=0.0001` (`pu`)
  - `v_poi`: `uint16`, configured as absolute voltage (`V`/`kV`) and converted to internal `kV`
  - `enable`, `start_command`, `stop_command`: `uint16`, `eng_per_count=1.0` (`raw`)
- Supported point unit tokens are case-insensitive and normalized (for example `%` -> `pc`, `kW` -> `kw`, `Mvar` -> `mvar`, `kV` -> `kv`).

## Plant Emulation Behavior
- Local mode starts one Modbus server per plant (`lib`, `vrfb`) simultaneously.
- Local plant start in control-engine local transport now attempts to restore emulator SoC from the latest persisted on-disk `soc_pu` for that plant (`data/YYYYMMDD_<sanitized_plant>.csv`), with fallback to `STARTUP_INITIAL_SOC_PU`.
- Control-engine->plant-agent coordination for this restore uses shared-state request/ack maps; `plant_agent.py` applies the seed to internal emulator state (`soc_kwh`) before enable.
- Plant emulation applies:
  - enable gating,
  - configured active/reactive power limits,
  - SoC boundary limiting.
- POI active/reactive values mirror battery outputs in current simplified model.
- POI voltage is simulated as an absolute constant in internal `kV` using `plants.*.model.poi_voltage_kv`, then encoded to the configured Modbus point unit (`V` or `kV`).

## Logging Behavior
- Root logger has three outputs:
  1. console,
  2. date-routed file `logs/YYYY-MM-DD_hil_scheduler.log` (record timestamp date in configured timezone),
  3. in-memory session list (retained for compatibility and lightweight in-process diagnostics).
- Session logs are bounded to latest 1000 entries.
- `data_fetcher_agent.py` logs explicit API fetch intent (`today` vs `tomorrow`), local request windows, and next-day gate state transitions (`waiting` / `eligible`) to reduce ambiguity around missing schedules.
- `data_fetcher_agent.py` now also prunes `api_schedule_df_by_plant` to the local current-day + next-day retention window so long-running sessions do not accumulate stale API schedule rows indefinitely.
- `scheduler_agent.py` also prunes manual override series to the local current-day + next-day window on day rollover; dashboard manual editor paths prune after each write/load.
- Manual override storage now encodes end-of-override using a terminal duplicate-value row; scheduler/effective-schedule helpers derive the exclusive manual end timestamp from that terminal row (no separate manual end-time shared-state maps).
- Dashboard logs tab behavior:
  - default selector is `today`,
  - `today` reads tail of current date file for live refresh,
  - historical log browsing reads selectable files from `logs/*.log`.
- Control engine publishes `plant_observed_state_by_plant` (cached `enable`, `p_battery`, `q_battery`, freshness/error metadata including `read_status` / `last_error` / `consecutive_failures`) so dashboard status callbacks avoid direct control-path Modbus polling.
- Control engine also publishes `plant_operating_state_by_plant` (physical `running|stopped|unknown` derived from observed enable) so dashboard can separate physical plant state from control transition state.
- Control engine also publishes `control_engine_status` (loop liveness/timestamps, queue metrics, active command metadata, last loop exception/last finished command) for Status-tab operator visibility.
- Scheduler and control-engine setpoint-write paths publish `dispatch_write_status_by_plant` (latest attempted/successful P/Q, source/status/error) for Status-tab sent-setpoint observability.
- Settings engine publishes manual/API/posting server-owned runtime states and `settings_engine_status` for command execution observability (currently mainly consumed by callbacks/tests; not yet surfaced in Status tab).

## Dashboard Styling Conventions
- Brand assets are served from Dash `assets/` (logo PNGs + local font files).
- Dashboard visual state is primarily class-driven in `dashboard_agent.py` and styled in `assets/custom.css`; a small number of inline style dictionaries remain in log/posting render helpers.
- Plot styling in `dashboard_agent.py` uses shared figure-theme helpers for consistent axes/grid/legend presentation without altering control callbacks.
- Historical `Plots` tab reuses the same figure helper/theme as Status plots for visual consistency; PNG downloads use client-side Plotly export (`window.Plotly.downloadImage`) and do not require `kaleido`.
- Shared plant figures are now 4-row plots (P / SoC / Q / Voltage) and can render P/Q setpoint traces from recorded measurement columns when no schedule dataframe is supplied (used by historical `Plots`).
- Status-tab figures call the same helper with an explicit local `today..day+2` x-window and a current-time vertical dashed line so live plots remain focused on immediate context while preserving historical browsing in `Plots`.
- Dashboard callbacks derive optional voltage y-padding from `plants.*.model.poi_voltage_kv`; custom voltage y-range is only applied for low-voltage plants (`< 10 kV`) using `5%` nominal padding, otherwise Plotly autorange is used.
- Manual Schedule tab now uses a responsive split layout (plots + compact editor), with dense operator-focused controls and compact breakpoint-row inputs.
- Manual per-series plots now overlay staged editor and applied server schedules (`Staged (Editor)` vs `Applied (Server)`) so command-driven activation/update differences are visible in the UI.
- Manual editor forces a terminal `end` row for non-empty schedules; the `end` row setpoint is UI-only (`end` label) and is stored/sent as a terminal duplicate-value numeric row.
- Manual editor row times are auto-clamped forward to maintain a minimum `60s` gap between rows (including terminal `end` row) instead of failing on non-increasing edits.
- Empty manual editor selections default the start datetime to the next `10-minute` local boundary.
- Historical `Plots` tab range selection is resilient to stale/default slider values: helper clamping treats fully out-of-domain selections (including the layout placeholder `[0, 1]`) as invalid and restores full discovered history span.
- Historical `Plots` tab availability timeline (`LIB`/`VRFB`) is intentionally compacted (reduced figure height/margins, lower legend placement) to minimize vertical whitespace.
- Current operator-requested theme constraints:
  - white page background,
  - flatter surfaces with minimal corner radius,
  - menu-style tab strip (no enclosing tab-shell card),
  - tab content cards aligned to tab strip width (no side margins),
  - non-signature i-STENTORE logotype in header,
  - flat green/red control buttons,
  - higher-contrast toggle selected-state pill.

## Timezone and Persistence Conventions
- All runtime schedule/measurement timestamps are timezone-aware.
- Naive timestamps are interpreted in configured timezone when normalized.
- Measurement CSV uses ISO 8601 timestamps with timezone offset.
- API posting timestamps are normalized to UTC ISO format.

## Operational Constraints
- Threaded model requires short lock sections and external I/O outside locks.
- `measurement_agent.py` lock-discipline cleanup now also covers `flush_pending_rows()` pending-row swap/process/merge flow (shorter lock sections during CSV flush prep).
- Local SoC restore handshake is best-effort by design: control-engine start waits briefly for plant-agent ack and logs timeout, but still proceeds with plant enable/start sequence.
- Control command execution is serialized through a bounded FIFO queue in shared state; high-latency stop/transport flows can delay later queued commands by design in this first pass.
- Dashboard status controls now depend on control-engine observed-state cache freshness (`stale` marker) rather than direct Modbus reads; stale cache displays `Unknown` for Modbus enable.
- Dashboard Status tab health lines are server-published-state-only (control engine + observed-state cache) and include queue/backlog and per-plant Modbus reachability/read-error diagnostics.
- Dashboard Status plant cards now also depend on `dispatch_write_status_by_plant` and `scheduler_running_by_plant` to render independent dispatch send/paused state and last write info.
- Scheduler-originated dispatch write status attempts now include readback reconciliation telemetry in `last_scheduler_context` (compare source, readback ok/mismatch flags); current dashboard summary displays a compact inline `RB P/Q=...` hint only when the latest dispatch attempt source is `scheduler`.
- Plant start (`plant.start`) no longer auto-enables `scheduler_running_by_plant`; per-plant dispatch send control is independent and command-driven (`plant.dispatch_enable` / `plant.dispatch_disable`).
- Dispatch pause semantics intentionally freeze the last plant setpoint (scheduler stops writing; no automatic zeroing on pause).
- Manual schedule editor persists draft series in dashboard-owned runtime draft maps; scheduler dispatch uses server-applied manual series activated through settings commands.
- Manual settings command payloads serialize the full numeric manual series (including terminal duplicate row) and do not carry a separate end timestamp field.
- Manual draft maps are shared across dashboard sessions (single-operator assumption in current runtime; per-session draft isolation deferred).
- API connect/disconnect is no longer equivalent to setting/clearing `api_password`; password storage and connection runtime state are separate.
- `api_connection_runtime.state` (including `error`) is now runtime-owned and recomputed from command transitions plus `fetch_health` / `posting_health`; dashboard API UI renders this state directly.
- Measurement posting queue is in-memory only; it does not persist across restarts.
- Measurement compression applies only to new runtime writes; no automatic backfill is performed for historical dense CSV files.
- Historical plots tab reads `data/*.csv` directly on demand; large datasets may increase dashboard callback latency because there is no persistent history index/cache yet.
- API schedule runtime retention and manual override runtime retention are both bounded to a local two-day window (`current day + next day`) during active runtime.
- The dashboard assumes both logical plants are always present in runtime state.
- API auth renewal is reactive (on `401`/`403`) with one retry per request path; no proactive token TTL refresh exists.
