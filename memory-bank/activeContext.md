# Active Context: HIL Scheduler

## Current Focus
Split schedule management into two independent schedules: Manual and API.

## Recent Changes (2026-02-01)

### Critical Bug Fix: Lock Contention Resolved
**Problem:** Dashboard UI freezing with "Updating" status, slow tab switching and button response.

**Root Cause:** `scheduler_agent.py` held the shared data lock for too long:
- Lock held during `asof()` DataFrame lookup
- Lock held during Modbus write operations
- This blocked the dashboard callbacks from accessing shared data

**Solution:** Minimized lock time in scheduler_agent.py:
- Lock now only held to get schedule reference (~microseconds)
- All operations (asof lookup, Modbus writes) happen outside lock
- Multiple threads can safely read DataFrames simultaneously

**Result:** UI is now responsive, no more "Updating" delays.

### Major Architecture Refactoring
Split the monolithic schedule management into two decoupled schedules:

**Before:**
- Single `schedule_final_df` managed by complex `ScheduleManager` class
- Polling-based mode switching caused timing issues
- Dashboard, Data Fetcher, and Scheduler tightly coupled through ScheduleManager

**After:**
- Two independent schedules: `manual_schedule_df` and `api_schedule_df`
- Data Fetcher agent completely decoupled - just fetches API data
- Dashboard manages manual schedule directly
- Scheduler reads `active_schedule_source` to choose which schedule to use

### Three-Tab Dashboard Structure
- **Tab 1: Manual Schedule** - Random generation, CSV upload, preview/accept
- **Tab 2: API Schedule** - Password input, connection status, API schedule preview
- **Tab 3: Status & Plots** - Active source selector, live graphs, system status

### Technical Changes
1. **[`hil_scheduler.py`](hil_scheduler.py)**:
   - New shared data: `manual_schedule_df`, `api_schedule_df`
   - New: `active_schedule_source` ('manual' or 'api')
   - New: `api_password` (set by dashboard, read by data fetcher)
   - New: `data_fetcher_status` (for dashboard display)

2. **[`data_fetcher_agent.py`](data_fetcher_agent.py)** (Completely rewritten):
   - Simple loop: wait for password → connect → fetch today + tomorrow → update `api_schedule_df`
   - No mode polling, no ScheduleManager dependency
   - Updates `data_fetcher_status` for dashboard

3. **[`scheduler_agent.py`](scheduler_agent.py)**:
   - Reads `active_schedule_source` from shared data
   - Uses appropriate schedule (`manual_schedule_df` or `api_schedule_df`)
   - Logs when source changes

4. **[`dashboard_agent.py`](dashboard_agent.py)** (Completely rewritten):
   - Three-tab structure
   - Tab 1: Direct management of `manual_schedule_df`
   - Tab 2: Password input for `api_password`, displays `data_fetcher_status`
   - Tab 3: Active source selector, live graphs

5. **New: [`manual_schedule_manager.py`](manual_schedule_manager.py)**:
   - Stateless utility functions for random generation and CSV loading
   - Simple functions: `generate_random_schedule()`, `load_csv_schedule()`, `append_schedules()`

## Previous Changes

### Schedule Creation Simplification (Earlier 2026-02-01)
Merged Random Schedule and CSV Upload into a single "Manual" mode.

### Dashboard UI Redesign (2026-01-31)
- Complete UI overhaul with professional light theme
- Preview workflow: Generate preview → Review diff → Accept to commit

## Next Steps
- Test the new three-tab dashboard
- Verify data fetcher correctly fetches and updates API schedule
- Test manual schedule generation and CSV upload
- Verify scheduler correctly switches between sources
- Monitor for any race conditions with the new decoupled architecture

## Architecture Notes

### New Data Flow
```
Manual Path:
  Dashboard (Tab 1) → manual_schedule_manager → manual_schedule_df

API Path:
  Dashboard (Tab 2: set password) → Data Fetcher → api_schedule_df

Scheduler Path:
  Reads active_schedule_source → Reads manual_df OR api_df → Plant
```

### Decoupling Benefits
1. **No polling delays**: Data Fetcher runs independently
2. **Clear separation**: Each component has one responsibility
3. **Easier debugging**: State changes are explicit in shared data
4. **Simpler code**: Removed complex ScheduleManager with callbacks

### Key Files
- [`dashboard_agent.py`](dashboard_agent.py): Three-tab dashboard
- [`data_fetcher_agent.py`](data_fetcher_agent.py): Decoupled API fetcher
- [`scheduler_agent.py`](scheduler_agent.py): Source-aware scheduler
- [`manual_schedule_manager.py`](manual_schedule_manager.py): Schedule utilities
- [`hil_scheduler.py`](hil_scheduler.py): Updated shared data structure
