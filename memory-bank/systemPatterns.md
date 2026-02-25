# System Patterns: HIL Scheduler

## Canonical Runtime Contracts

### Plant and Selector Model
- Logical plant IDs are fixed: `lib`, `vrfb`.
- Global runtime selectors:
  - `transport_mode` in `{local, remote}`.
- Manual schedule override model:
  - API schedule is the dispatch base.
  - Manual schedules are stored as four independent series (`lib_p`, `lib_q`, `vrfb_p`, `vrfb_q`).
  - Dashboard now maintains separate manual draft series (`manual_schedule_draft_series_df_by_key`) for editor/load/save UX.
  - Manual drafts are currently shared across dashboard sessions (single-operator assumption; per-session isolation deferred).
  - Settings engine applies/activates server-owned manual series into `manual_schedule_series_df_by_key` + merge flags via commands.
  - Per-series booleans control whether each manual series overwrites the corresponding API signal in dispatch.
- Per-plant runtime gates:
  - `scheduler_running_by_plant[plant_id]`.
  - `measurements_filename_by_plant[plant_id]` (`None` means recording off).
- Runtime control command channel:
  - FIFO `control_command_queue` for UI-issued operator intents.
  - command lifecycle tracking via `control_command_status_by_id`, `control_command_history_ids`, `control_command_active_id`.
- Runtime plant observed-state cache:
  - `plant_observed_state_by_plant[plant_id]` publishes cached `enable`, `p_battery`, `q_battery`, freshness timestamps, and stale/error markers.
- Runtime control-engine health cache:
  - `control_engine_status` publishes loop liveness/timestamps, queue metrics, active command metadata, last finished command, and last loop exception for UI observability.
- Normalized per-plant Modbus endpoints expose:
  - connection settings (`host`, `port`, required `byte_order`, required `word_order`)
  - structured Modbus `points` metadata (address, format, access, unit, scale, derived widths)
- Runtime Modbus I/O is holding-register-only; point codecs are shared across scheduler, sampling, dashboard helpers, and local emulation.
- Point `unit` is operational (not display-only): runtime helpers convert between Modbus engineering values and internal units (`soc_pu`, `kW`/`kvar`, `v_poi_kV`).

### Authoritative Shared State
`hil_scheduler.py` initializes this runtime contract via `build_initial_shared_data(config)`:

```python
shared_data = {
    "session_logs": [],
    "log_lock": threading.Lock(),

    "manual_schedule_df_by_plant": {"lib": pd.DataFrame(), "vrfb": pd.DataFrame()},  # derived compatibility/display cache
    "manual_schedule_draft_series_df_by_key": { ... },  # dashboard-owned editor drafts
    "manual_schedule_series_df_by_key": {
        "lib_p": pd.DataFrame(), "lib_q": pd.DataFrame(),
        "vrfb_p": pd.DataFrame(), "vrfb_q": pd.DataFrame(),
    },
    "manual_schedule_merge_enabled_by_key": {
        "lib_p": False, "lib_q": False, "vrfb_p": False, "vrfb_q": False,
    },
    "manual_series_runtime_state_by_key": { ... },
    "api_schedule_df_by_plant": {"lib": pd.DataFrame(), "vrfb": pd.DataFrame()},

    "transport_mode": "local",

    "scheduler_running_by_plant": {"lib": False, "vrfb": False},
    "plant_transition_by_plant": {"lib": "stopped", "vrfb": "stopped"},

    "measurements_filename_by_plant": {"lib": None, "vrfb": None},
    "current_file_path_by_plant": {"lib": None, "vrfb": None},
    "current_file_df_by_plant": {"lib": pd.DataFrame(), "vrfb": pd.DataFrame()},
    "pending_rows_by_file": {},
    "measurements_df": pd.DataFrame(),

    "measurement_post_status": {
        "lib": {
            "posting_enabled": False,
            "last_success": None,
            "last_attempt": None,
            "last_error": None,
            "pending_queue_count": 0,
            "oldest_pending_age_s": None,
            "last_enqueue": None,
        },
        "vrfb": {
            "posting_enabled": False,
            "last_success": None,
            "last_attempt": None,
            "last_error": None,
            "pending_queue_count": 0,
            "oldest_pending_age_s": None,
            "last_enqueue": None,
        },
    },
    "local_emulator_soc_seed_request_by_plant": {"lib": None, "vrfb": None},
    "local_emulator_soc_seed_result_by_plant": {
        "lib": {"request_id": None, "status": "idle", "soc_pu": None, "message": None},
        "vrfb": {"request_id": None, "status": "idle", "soc_pu": None, "message": None},
    },
    "posting_runtime": { ... },

    "api_password": None,
    "api_connection_runtime": { ... },
    "data_fetcher_status": {
        "connected": False,
        "today_fetched": False,
        "tomorrow_fetched": False,
        "today_date": None,
        "tomorrow_date": None,
        "today_points": 0,
        "tomorrow_points": 0,
        "today_points_by_plant": {"lib": 0, "vrfb": 0},
        "tomorrow_points_by_plant": {"lib": 0, "vrfb": 0},
        "last_attempt": None,
        "error": None,
    },

    "transport_switching": False,
    "control_command_queue": queue.Queue(maxsize=128),
    "control_command_status_by_id": {},
    "control_command_history_ids": [],
    "control_command_active_id": None,
    "control_command_next_id": 1,
    "plant_observed_state_by_plant": {
        "lib": {
            "enable_state": None, "p_battery_kw": None, "q_battery_kvar": None,
            "last_attempt": None, "last_success": None, "error": None,
            "read_status": "unknown", "last_error": None, "consecutive_failures": 0, "stale": True,
        },
        "vrfb": {
            "enable_state": None, "p_battery_kw": None, "q_battery_kvar": None,
            "last_attempt": None, "last_success": None, "error": None,
            "read_status": "unknown", "last_error": None, "consecutive_failures": 0, "stale": True,
        },
    },
    "control_engine_status": {
        "alive": False, "last_loop_start": None, "last_loop_end": None, "last_observed_refresh": None,
        "last_exception": None, "active_command_id": None, "active_command_kind": None,
        "active_command_started_at": None, "last_finished_command": None, "queue_depth": 0,
        "queued_count": 0, "running_count": 0, "failed_recent_count": 0,
    },
    "settings_command_queue": queue.Queue(maxsize=128),
    "settings_command_status_by_id": {},
    "settings_command_history_ids": [],
    "settings_command_active_id": None,
    "settings_command_next_id": 1,
    "settings_engine_status": { ... },

    "lock": threading.Lock(),
    "shutdown_event": threading.Event(),
    "log_file_path": None,
}
```

## Agent Responsibilities
- `data_fetcher_agent.py`: fetches day-ahead schedules and updates per-plant API maps + status.
- `scheduler_agent.py`: dispatches P/Q setpoints per plant from merged effective schedule (API base + enabled manual overrides) and per-plant gate.
- `plant_agent.py`: local emulation server for each logical plant with SoC and power limit behavior.
- `measurement_agent.py`: measurement sampling, recording, cache updates, API posting queue/telemetry.
- `control_engine_agent.py`: consumes UI command queue, executes start/stop/fleet/transport/record control flows, owns control-path Modbus I/O, and publishes cached plant observed state.
- `settings_engine_agent.py`: consumes settings command queue and executes manual activation/update/inactivation, API connect/disconnect, and posting policy enable/disable.
- `dashboard_agent.py`: UI layout/callbacks, command enqueueing, manual override editor/plots, status plots, logs, and short-lived click-feedback transition overlay.
- `dashboard_history.py`: helper functions for dashboard historical measurement scan/index, range clamping, file loading/cropping, and CSV serialization.
- `dashboard_control.py`: shared safe-stop + transport-switch control-flow helpers reused by control engine execution.

## Operational Patterns

### Safe Stop Contract
- Safe stop is the standard stop primitive for control-engine stop operations.
- Sequence:
  1. Set dispatch gate off for the plant.
  2. Write zero active and reactive setpoints.
  3. Wait for measured battery active/reactive values below threshold.
  4. Disable plant.
- Return payload:
  - `{ "threshold_reached": bool, "disable_ok": bool }`.

### Transport Switching
- Transport switch is modal-confirmed and safety-gated.
- Confirm path:
  1. Dashboard enqueues transport-switch intent.
  2. Control engine sets transport switching flag.
  3. Control engine safe-stops both plants.
  4. Control engine applies transport selector update.
  5. Control engine clears switching flag.

### Fleet Start/Stop Actions
- Status tab top card provides confirmation-gated bulk controls.
- `Start All` sequence:
  1. Dashboard enqueues fleet-start intent after confirmation.
  2. Control engine enables recording for both plants (`measurements_filename_by_plant[*]`).
  3. Control engine triggers each plant start flow (gate on, enable command, initial setpoint send).
- `Stop All` sequence:
  1. Dashboard enqueues fleet-stop intent after confirmation.
  2. Control engine safe-stops both plants.
  3. Control engine clears recording flags for both plants.

### Control Command Execution and Status Cache
- Dashboard control callbacks enqueue normalized commands (`plant.start`, `plant.stop`, `plant.record_start`, `plant.record_stop`, `fleet.start_all`, `fleet.stop_all`, `transport.switch`) into `control_command_queue`.
- `control_engine_agent.py` processes commands serially (FIFO) and updates command lifecycle status:
  - `queued` -> `running` -> terminal (`succeeded` / `failed` / `rejected`).
- Queue overflow is handled as a terminal rejected status (`message="queue_full"`), preserving UI responsiveness.
- Command status history is bounded (recent IDs/statuses retained, oldest pruned).
- Generic shared bookkeeping now lives in `command_runtime.py`; control/settings command runtime modules are thin wrappers over engine-specific shared-state keys.

### Plant Observed-State Cache and UI Transition Semantics
- Control engine performs periodic best-effort Modbus reads for `enable`, `p_battery`, and `q_battery` and publishes `plant_observed_state_by_plant`.
- Cache entries track `last_attempt`, `last_success`, `error`, `read_status`, `last_error`, `consecutive_failures`, and `stale`.
- Dashboard Status tab consumes this cache and does not perform direct Modbus polling for control/status paths.
- Plant state semantics in UI/runtime:
  - `starting` / `stopping` are authoritative runtime transition states owned by the control engine (`plant_transition_by_plant`).
  - `running` / `stopped` are confirmed when Modbus `enable` reflects `1` / `0`.
  - Dashboard applies a short immediate click-feedback overlay (`starting`/`stopping`) before falling back to server transition state, then Modbus-confirmed state.

### Control-Engine Health Surfacing
- Control engine publishes `control_engine_status` every loop so dashboard UI can render queue/backlog and runtime health without scanning raw command status maps.
- Published queue metrics are derived from shared command lifecycle state and include:
  - `queue_depth`,
  - `queued_count`,
  - `running_count`,
  - `failed_recent_count` (rolling recent terminal `failed`/`rejected` count).
- Dashboard Status tab top card renders:
  - control-engine liveness/active-command summary,
  - queue backlog summary,
  - per-plant Modbus read/connectivity/freshness diagnostics.

### Settings Command Pattern (Manual/API/Posting)
- Dashboard manual editor/load/save remains dashboard-owned and writes draft series only.
- High-level settings transitions are command-driven:
  - manual per-series `Activate` / `Inactivate` / `Update`,
  - API `Connect` / `Disconnect`,
  - Posting policy `Enable` / `Disable`.
- Dashboard buttons use short immediate click-feedback transition overlays (e.g. `Activating...`, `Connecting...`) and then render server-owned settings runtime state.
- `Disconnect` intentionally stops API runtime activity but preserves stored `api_password`.
- `api_connection_runtime.state` is fully runtime-owned:
  - `settings_engine_agent.py` publishes connect/disconnect transitions and probe outcomes,
  - `data_fetcher_agent.py` publishes `fetch_health`,
  - `measurement_agent.py` publishes `posting_health`,
  - dashboard renders `api_connection_runtime` without deriving API `Error` from telemetry.
- `measurement_agent.py` posting-effective gate now depends on:
  - stored password presence,
  - posting policy (`posting_runtime.policy_enabled`),
  - API connection runtime state (connected/error vs intentionally disconnected).

### Local Plant Start SoC Restore
- Applies only when transport mode is `local`.
- Control-engine start flow resolves a target SoC before enable:
  1. Read latest persisted non-null `soc_pu` for the plant from `data/*.csv` (by highest timestamp).
  2. Fallback to `STARTUP_INITIAL_SOC_PU` if none exists.
  3. Publish a seed request into `local_emulator_soc_seed_request_by_plant[plant_id]`.
  4. Wait briefly for `plant_agent.py` acknowledgement in `local_emulator_soc_seed_result_by_plant[plant_id]`.
  5. Continue normal enable + initial setpoint start sequence even if the ack times out (warning logged).
- `plant_agent.py` is authoritative for applying the seed because it owns internal emulator SoC state (`soc_kwh`).
- Seed requests are rejected/skipped while the plant is enabled to avoid mid-run SoC resets.

### Scheduler Dispatch Selection
- Scheduler always resolves dispatch from a merged effective schedule per plant:
  - API schedule (`api_schedule_df_by_plant[plant_id]`) is the base.
  - API staleness cutoff still applies via `ISTENTORE_SCHEDULE_PERIOD_MINUTES` and can zero stale API base values.
  - Manual overrides are resolved per signal using the manual series maps (`*_p`, `*_q`) and as-of lookup.
  - If `manual_schedule_merge_enabled_by_key[series_key]` is `True` and a manual as-of value exists, it overwrites the corresponding API signal (`P` or `Q`) only.
- Per-plant dispatch gate behavior and Modbus write deduping are unchanged.

### Manual Override Schedule Sanitization and Editor Pattern
- Manual overrides are stored as four independent absolute-time series (`setpoint` column, datetime index).
- Dashboard Manual tab editor presents relative breakpoints (`HH:MM:SS` + setpoint) for one selected series at a time.
- Editor CSV format is relative-time only (`hours, minutes, seconds, setpoint`) and must start at `00:00:00`.
- First breakpoint row is always `00:00:00` for non-empty schedules.
- Manual series are sanitized to local `[today 00:00, day+2 00:00)`:
  - immediately after editor/CSV mutations,
  - on scheduler day rollover.
- `manual_schedule_df_by_plant` is rebuilt from the four manual series as a derived compatibility/display cache.

### API Schedule Fetching and Day Rollover
- `data_fetcher_agent.py` always attempts `today` day-ahead fetch when `data_fetcher_status.today_fetched` is false (no time-of-day gate).
- `tomorrow` day-ahead fetch attempts are gated by normalized config key `ISTENTORE_TOMORROW_POLL_START_TIME` (local configured timezone wall-clock).
- `api_schedule_df_by_plant` retention is bounded to the local calendar window `[today 00:00, day+2 00:00)` to prevent indefinite growth across long-running sessions.
- Fetcher logs include explicit request purpose (`today`/`tomorrow`), local request window, and trigger reason to support operator troubleshooting.
- Next-day gate visibility is logged on state transitions (`waiting` -> `eligible`) per target `tomorrow_date` to avoid log spam.
- Partial API windows (one plant missing data) are still published into `api_schedule_df_by_plant` for available plants, but `*_fetched` remains false and `data_fetcher_status.error` is set to a window-specific incomplete-data message so retries continue and the dashboard shows the issue.
- `today` refetch writes merge into existing in-window API rows so already-fetched tomorrow rows are preserved until replaced or pruned out of the retention window.
- Day rollover reconciliation may promote a previously fetched `tomorrow` status window into `today` status if dates align; the new `tomorrow` window status is reset.

### Measurement Triggering and Persistence
- Measurement timing uses anchored monotonic steps to prevent drift.
- One measurement attempt max per step.
- Missed intermediate steps are skipped.
- Row timestamps are scheduled step times, not completion times.
- Recording is file-routed by row timestamp:
  - destination `data/YYYYMMDD_<plantname>.csv`.
- Recorded measurement schema includes voltage as absolute `v_poi_kV` (breaking migration from legacy `v_poi_pu`).
- Compression behavior for recording rows is tolerance-based and boundary-preserving:
  - null boundary rows are always retained,
  - stable real-value runs retain first + latest points,
  - tolerance and keep-gap decisions are evaluated against the last kept real row (prevents drift),
  - a configurable keep-gap threshold (`recording.compression.max_kept_gap_s`) forces retention when stable runs exceed the interval,
  - latest point of active runs may replace the mutable in-memory tail row.
- During periodic non-force flushes, one tail row per active recording file is retained in memory to preserve first/latest segment semantics across flush boundaries.
- Current-day plot cache is maintained per plant in memory.
- Historical plot browsing reads persisted CSVs from `data/*.csv` on demand and does not depend on current-day in-memory caches.

### API Measurement Posting
- Posting is owned by `measurement_agent.py` and is independent from sample/flush cadence.
- Voltage posting uses measured `v_poi_kV * 1000` to send volts (no reconstruction from per-unit and model base voltage).
- Gate conditions:
  - runtime posting policy `posting_runtime.policy_enabled` is true,
  - API password exists,
  - config default `ISTENTORE_POST_MEASUREMENTS_IN_API_MODE` seeds startup policy state.
- Queue behavior:
  - bounded in-memory queue,
  - exponential retry backoff,
  - oldest-drop on overflow.
- Per-plant telemetry is continuously updated in `measurement_post_status`.

### API Authentication and Token Renewal
- `istentore_api.py` owns API token lifecycle for schedule fetch and measurement post calls.
- Setting/changing password invalidates current token immediately (`set_password()` clears token).
- Authentication is lazy: if no token exists, login occurs before request.
- Auth-retry policy is reactive and bounded:
  - HTTP `401` or `403` clears token and triggers one re-authentication retry.
  - If retry also fails with auth error, request fails with `IstentoreAPIError`.
- No time-based proactive token refresh is configured.

### Log Routing and Dashboard Log Views
- `logger_config.py` routes file output by each record timestamp date in configured timezone (`TIMEZONE_NAME`), not process start date.
- Active destination path is surfaced in `shared_data["log_file_path"]` and updates when date rolls.
- Dashboard logs callback contract:
  - default selector value is `today`,
  - `today` reads tail of current date file for live refresh on interval ticks,
  - historical file selections pause interval-driven refresh until selection changes.
- Legacy selector value `current_session` is normalized to `today` for compatibility.

### Dashboard Status Summary Pattern
- Status tab inline API summary shows connectivity plus per-plant fetched-point counts for both `today` and `tomorrow` windows.
- This status line is intended as quick fetch-health visibility without requiring navigation to API tab.
- Status tab plots are display-cropped to local `current day + next day` and render the merged effective schedule (API base + enabled manual overrides).

### Dashboard Historical Plots Pattern
- `Plots` tab scans `data/*.csv` on a slower dedicated interval (separate from the main 1s UI refresh interval).
- File discovery maps known plant filename suffixes (sanitized plant names) to `lib`/`vrfb`; unknown suffixes are ignored.
- Global timeline range is derived from actual CSV timestamps (not filenames).
- Range selection is represented as epoch milliseconds and clamped to the discovered global range.
- If a slider selection is fully outside the current discovered domain (for example the initial layout placeholder `[0, 1]` before history bounds load), the helper defaults back to the full discovered range instead of collapsing to one edge.
- Historical plant figures reuse the same multi-panel plot helper as live status plots, with empty schedule overlays and cropped measurement traces only.
- The top availability timeline intentionally uses a compact figure height/margins because it only renders two categorical lanes (`LIB`, `VRFB`).
- CSV export serializes cropped rows in canonical measurement column order.
- PNG export is client-side via Plotly browser APIs using the already-rendered graph.

## Time and Timestamp Conventions
- Runtime timestamps are timezone-aware in configured timezone.
- API schedule delivery periods are parsed as UTC then converted to configured timezone.
- Persisted CSV measurement timestamps are ISO 8601 with timezone offset.
- Posted measurement timestamps are strict UTC ISO (`+00:00`).
- Log file day boundaries follow configured timezone date and record timestamps.

## Locking Discipline
- Target contract:
  - hold `shared_data["lock"]` only for short reference reads/writes,
  - perform Modbus I/O, API I/O, file I/O, and dataframe-heavy transforms outside the lock,
  - keep dashboard callbacks responsive by avoiding long lock sections.
- Recent cleanup:
  - `measurement_agent.py` aggregate-cache rebuild and current-file cache upsert now use snapshot-under-lock / dataframe compute outside lock / write-back-under-lock.
  - `measurement_agent.py` `flush_pending_rows()` now swaps pending rows under lock, prepares retained/flush snapshots outside lock, then merges retained/failed rows back under lock.
- Remaining follow-up:
  - continue auditing less critical measurement/cache paths for the same lock-discipline pattern.
- Engine command lifecycle bookkeeping is shared by `engine_command_cycle_runtime.py` (mark running, execute, exception->status, mark finished, publish `last_finished_command`, `task_done()`), while control/settings engine loops remain separate.
- Runtime settings command channel:
  - FIFO `settings_command_queue` for UI-issued manual/API/posting intents.
  - command lifecycle tracking via `settings_command_status_by_id`, `settings_command_history_ids`, `settings_command_active_id`.
- Runtime settings-engine health cache:
  - `settings_engine_status` publishes queue/liveness/active command metadata for settings-command execution.
- Runtime settings state caches:
  - `manual_series_runtime_state_by_key` (per-series `inactive|activating|active|inactivating|updating|error` state + last error/command metadata),
  - `api_connection_runtime` (`disconnected|connecting|connected|disconnecting|error`; password storage is separate) including nested `fetch_health` / `posting_health` sub-health inputs used to recompute the top-level connection state,
  - `posting_runtime` (`disabled|enabling|enabled|disabling|error` policy state).
