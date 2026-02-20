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
`hil_scheduler.py` initializes this runtime contract:

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
- Current-day plot cache is maintained per plant in memory.

### API Measurement Posting
- Posting is owned by `measurement_agent.py` and is independent from sample/flush cadence.
- Gate conditions:
  - global source is API,
  - API password exists,
  - posting flag enabled in config.
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

## Time and Timestamp Conventions
- Runtime timestamps are timezone-aware in configured timezone.
- API schedule delivery periods are parsed as UTC then converted to configured timezone.
- Persisted CSV measurement timestamps are ISO 8601 with timezone offset.
- Posted measurement timestamps are strict UTC ISO (`+00:00`).

## Locking Discipline
- Hold `shared_data["lock"]` only for short reference reads/writes.
- Perform dataframe operations, Modbus I/O, API I/O, and file I/O outside the lock.
- Keep dashboard callbacks responsive by avoiding long lock sections.
