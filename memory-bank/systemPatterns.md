# System Patterns: HIL Scheduler

## Canonical Runtime Contracts

### Plant and Selector Model
- Logical plant IDs are fixed: `lib`, `vrfb`.
- Global runtime selectors:
  - `active_schedule_source` in `{manual, api}`.
  - `transport_mode` in `{local, remote}`.
- Per-plant runtime gates:
  - `scheduler_running_by_plant[plant_id]`.
  - `measurements_filename_by_plant[plant_id]` (`None` means recording off).

### Authoritative Shared State
`hil_scheduler.py` initializes this runtime contract via `build_initial_shared_data(config)`:

```python
shared_data = {
    "session_logs": [],
    "log_lock": threading.Lock(),

    "manual_schedule_df_by_plant": {"lib": pd.DataFrame(), "vrfb": pd.DataFrame()},
    "api_schedule_df_by_plant": {"lib": pd.DataFrame(), "vrfb": pd.DataFrame()},

    "active_schedule_source": "manual",
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
    "measurement_posting_enabled": True,

    "api_password": None,
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

    "schedule_switching": False,
    "transport_switching": False,

    "lock": threading.Lock(),
    "shutdown_event": threading.Event(),
    "log_file_path": None,
}
```

## Agent Responsibilities
- `data_fetcher_agent.py`: fetches day-ahead schedules and updates per-plant API maps + status.
- `scheduler_agent.py`: dispatches P/Q setpoints per plant based on active source and per-plant gate.
- `plant_agent.py`: local emulation server for each logical plant with SoC and power limit behavior.
- `measurement_agent.py`: measurement sampling, recording, cache updates, API posting queue/telemetry.
- `dashboard_agent.py`: user controls, safe-stop flows, source/transport switch modals, plots, logs.
- `dashboard_history.py`: helper functions for dashboard historical measurement scan/index, range clamping, file loading/cropping, and CSV serialization.
- `dashboard_control.py`: shared safe-stop + global switch control-flow helpers used by dashboard callbacks.

## Operational Patterns

### Safe Stop Contract
- Safe stop is the standard stop primitive for dashboard operations.
- Sequence:
  1. Set dispatch gate off for the plant.
  2. Write zero active and reactive setpoints.
  3. Wait for measured battery active/reactive values below threshold.
  4. Disable plant.
- Return payload:
  - `{ "threshold_reached": bool, "disable_ok": bool }`.

### Source and Transport Switching
- Both switches are modal-confirmed and safety-gated.
- Confirm path:
  1. Set switching flag.
  2. Safe-stop both plants.
  3. Apply selector update.
  4. Clear switching flag.

### Fleet Start/Stop Actions
- Status tab top card provides confirmation-gated bulk controls.
- `Start All` sequence:
  1. Enable recording for both plants (`measurements_filename_by_plant[*]`).
  2. Trigger each plant start flow (gate on, enable command, initial setpoint send).
- `Stop All` sequence:
  1. Safe-stop both plants.
  2. Clear recording flags for both plants.

### Scheduler Dispatch Selection
- Scheduler chooses map by `active_schedule_source`.
- Manual source reads `manual_schedule_df_by_plant`.
- API source reads `api_schedule_df_by_plant`.
- API source applies stale-row cutoff via `ISTENTORE_SCHEDULE_PERIOD_MINUTES`; stale rows dispatch zero setpoints.

### Measurement Triggering and Persistence
- Measurement timing uses anchored monotonic steps to prevent drift.
- One measurement attempt max per step.
- Missed intermediate steps are skipped.
- Row timestamps are scheduled step times, not completion times.
- Recording is file-routed by row timestamp:
  - destination `data/YYYYMMDD_<plantname>.csv`.
- Compression behavior for recording rows is tolerance-based and boundary-preserving:
  - null boundary rows are always retained,
  - stable real-value runs retain first + latest points,
  - latest point of active runs may replace the mutable in-memory tail row.
- During periodic non-force flushes, one tail row per active recording file is retained in memory to preserve first/latest segment semantics across flush boundaries.
- Current-day plot cache is maintained per plant in memory.
- Historical plot browsing reads persisted CSVs from `data/*.csv` on demand and does not depend on current-day in-memory caches.

### API Measurement Posting
- Posting is owned by `measurement_agent.py` and is independent from sample/flush cadence.
- Gate conditions:
  - runtime posting toggle `measurement_posting_enabled` is true,
  - global source is API,
  - API password exists,
  - config default `ISTENTORE_POST_MEASUREMENTS_IN_API_MODE` seeds startup toggle state.
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

### Dashboard Historical Plots Pattern
- `Plots` tab scans `data/*.csv` on a slower dedicated interval (separate from the main 1s UI refresh interval).
- File discovery maps known plant filename suffixes (sanitized plant names) to `lib`/`vrfb`; unknown suffixes are ignored.
- Global timeline range is derived from actual CSV timestamps (not filenames).
- Range selection is represented as epoch milliseconds and clamped to the discovered global range.
- Historical plant figures reuse the same multi-panel plot helper as live status plots, with empty schedule overlays and cropped measurement traces only.
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
- Current exception:
  - measurement cache update paths in `measurement_agent.py` still include some lock-scoped dataframe operations; this is tracked as a follow-up cleanup item.
