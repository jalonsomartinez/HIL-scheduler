# Active Context: HIL Scheduler

## Current Focus
Extended the HIL Scheduler with 3 selectable schedule modes:
1. **Random Mode**: Generate random schedules at configurable resolution
2. **CSV Mode**: Upload CSV files with custom start time via dashboard
3. **API Mode**: Fetch schedules from Istentore API with automatic polling for next day

Created new modules:
- `istentore_api.py`: Istentore API wrapper with session-based password handling
- `schedule_manager.py`: Central module for managing all schedule modes

## Recent Changes (2026-01-31)

### Extended Setpoint Modes Implementation

#### New Files Created
1. **`istentore_api.py`**: Istentore API wrapper class
   - Session-based password handling (password not stored, asked by dashboard)
   - `get_day_ahead_schedule(start_time, end_time)`: Fetch day-ahead market schedules
   - `get_mfrr_next_activation()`: Fetch next MFRR activation
   - `schedule_to_dataframe()`: Convert API response to pandas DataFrame
   - Automatic token refresh on 401 errors

2. **`schedule_manager.py`**: Central schedule management
   - `ScheduleMode` enum: RANDOM, CSV, API
   - `ScheduleManager` class:
     - `set_mode()`: Switch between modes
     - `generate_random_schedule()`: Mode 1 - Random schedules at 5-min resolution
     - `load_csv_schedule()`: Mode 2 - Load from CSV with start time offset
     - `fetch_current_day_schedule()`: Mode 3 - Fetch current day on API connect
     - `fetch_next_day_schedule()`: Mode 3 - Fetch next day (used in polling)
     - `append_schedule()`: Add data, replace only overlapping periods
     - `start_api_polling()`: Poll for next day starting at 17:30
     - `get_current_setpoint()`: Use asof() to find value before current time

#### Modified Files

1. **`data_fetcher_agent.py`**:
   - Now uses ScheduleManager instead of direct CSV generation
   - Handles mode changes triggered from dashboard via shared_data
   - Stores schedule_manager reference in shared_data for dashboard access
   - No upsampling - uses original resolution from source

2. **`dashboard_agent.py`**:
   - Added mode selection radio buttons (Random, CSV, API)
   - Added controls for each mode:
     - Random: Duration, min/max power inputs, Generate button
     - CSV: File upload, start date/time picker, Load button
     - API: Password input, Connect & Fetch button
   - Added schedule preview graph
   - Added Clear Schedule button
   - Added mode status display

3. **`config.yaml`**:
   - Added `istentore_api` section:
     - `base_url`: API endpoint
     - `email`: Fixed email (i-STENTORE)
     - `poll_interval_min`: 10 (polling interval)
     - `poll_start_time`: "17:30" (when to start polling for next day)
   - Added `schedule` section:
     - `default_min_power_kw`: -1000
     - `default_max_power_kw`: 1000
     - `default_q_power_kvar`: 0
     - `default_resolution_min`: 5

4. **`config_loader.py`**:
   - Added loading of `istentore_api` section
   - Added loading of `schedule` section

5. **`hil_scheduler.py`**:
   - Runs indefinitely (no fixed end time)
   - Uses new shared_data structure with `schedule_manager` reference
   - Added `schedule_mode` and `schedule_mode_params` to shared_data

### Key Design Decisions

1. **Flexible Time Resolution**: Schedule DataFrame preserves original resolution
   - API: 5-minute intervals (from market periods)
   - CSV: Whatever resolution the CSV has
   - Random: Configurable (default 5 minutes)

2. **Smart Data Replacement**: When new data is added:
   - Only overlapping time periods are replaced
   - Non-overlapping data is preserved
   - Allows accumulating schedules from multiple sources

3. **asof() Lookup**: Scheduler uses pandas `asof()` to find the value just before current time
   - Works correctly with any time resolution
   - No upsampling needed

4. **API Polling Logic**:
   - On API mode connect: Immediately fetch current day schedule
   - Start polling at 17:30 (configurable)
   - Poll every 10 minutes (configurable) until next day schedule available
   - Next day schedule is typically available around 18:00

5. **Password Handling**:
   - Password asked by dashboard when switching to API mode
   - Stored in memory only (session-persistent)
   - Cleared when application restarts
   - Re-asked when API mode is selected again

## Next Steps
1. Functional testing of the 3 schedule modes
2. Test API connection and polling
3. Test CSV upload with start time offset
4. Update README.md with new architecture documentation

## Architecture

```
Dashboard (Mode Selection UI)
    ↓ shared_data['schedule_mode']
Data Fetcher Agent → ScheduleManager
    ↓
    ├─ Mode 1: Random Generator → schedule_df
    ├─ Mode 2: CSV Loader → schedule_df
    └─ Mode 3: API Fetcher + Polling → schedule_df
    ↓ shared_data['schedule_final_df']
Scheduler Agent (uses asof()) → Plant Agent
```

## Configuration

### config.yaml - Istentore API Settings
```yaml
istentore_api:
  base_url: "https://3mku48kfxf.execute-api.eu-south-2.amazonaws.com/default"
  email: "i-STENTORE"
  poll_interval_min: 10
  poll_start_time: "17:30"

schedule:
  default_min_power_kw: -1000
  default_max_power_kw: 1000
  default_q_power_kvar: 0
  default_resolution_min: 5
```

## Important Patterns

### Schedule DataFrame Format
```python
# Index: datetime (flexible resolution)
# Columns:
#   - power_setpoint_kw: float
#   - reactive_power_setpoint_kvar: float (defaults to 0)

# Example (5-min from API):
#                          power_setpoint_kw  reactive_power_setpoint_kvar
# 2026-01-31 00:00:00                 100.0                           0.0
# 2026-01-31 00:05:00                 150.0                           0.0
```

### Mode Switching Flow
1. User selects mode in dashboard (radio button)
2. User fills mode-specific controls and clicks action button
3. Dashboard sets `shared_data['schedule_mode']` and `shared_data['schedule_mode_params']`
4. Data fetcher agent detects mode change and calls `schedule_manager.set_mode()`
5. Schedule manager generates/loads schedule and updates `schedule_final_df`
6. Scheduler agent picks up new schedule on next iteration

### Thread Safety
- `schedule_manager._lock`: Protects schedule DataFrame access
- `shared_data['lock']`: Used by agents for broader shared data access
- `schedule_manager._shutdown_event`: Used to stop API polling thread
