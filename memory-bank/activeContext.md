# Active Context: HIL Scheduler

## Current Focus
Simplified architecture by eliminating buffer and local state. Measurements now appear immediately in dashboard with no latency.

## Recent Changes (2026-02-01)

### Eliminated Buffer and Local State (Major Simplification)
Removed unnecessary buffering and caching layers to reduce latency and code complexity:

**Rationale:**
- Locks are held only for microseconds (reference assignments, not data processing)
- The measurement buffer added up to 1s delay before measurements appeared in shared_data
- The dashboard local state added another 1s delay before UI saw updates
- **Total latency reduction: up to 2 seconds**

**Files Modified:**
1. **[`measurement_agent.py`](measurement_agent.py)** - Removed buffer pattern:
   - ~~Removed `measurement_buffer` list~~
   - ~~Removed `flush_buffer_to_dataframe()` function~~
   - ~~Removed `FLUSH_INTERVAL_S` and `BUFFER_SIZE_LIMIT` constants~~
   - Now writes **directly** to `shared_data['measurements_df']` after each measurement
   - Lock held only for DataFrame reference assignment (microseconds)
   
2. **[`dashboard_agent.py`](dashboard_agent.py)** - Removed local state caching:
   - ~~Removed `local_state` dictionary~~
   - ~~Removed `sync_from_shared_data()` background thread~~
   - ~~Removed `sync_thread`~~
   - Callbacks now read **directly** from `shared_data` with brief locks
   - Only `last_modbus_status` remains as mutable state

**Benefits:**
- **Reduced latency**: Measurements appear immediately (no buffer delay)
- **Simpler code**: ~50 lines removed from each agent
- **No background threads** needed for data synchronization
- **Data freshness**: Dashboard always sees current state
- **Immediate visibility**: No 1-second sync delay

### Measurement File Management System
Implemented dynamic measurement file handling:

**Files Modified:**
1. **[`hil_scheduler.py`](hil_scheduler.py)**: Added `measurements_filename` to shared_data
2. **[`dashboard_agent.py`](dashboard_agent.py)**: Start button generates timestamped filename, Stop clears it
3. **[`measurement_agent.py`](measurement_agent.py)**: Filename polling + direct writes

**Key Features:**
- **Timestamped filenames**: `data/YYYYMMDD_HHMMSS_data.csv`
- **Filename stored in shared_data**: Dashboard sets it, agent polls it
- **Automatic file rotation**: On new Start, writes old data, clears DataFrame, starts new file
- **Poll every 1 second**: Agent checks for filename changes independently from measurement rate
- **Stop clears filename**: Sets to `None`, agent stops writing to disk
- **Files saved to `data/` folder**: Keeps project root clean

**Measurement Agent Architecture (Simplified):**
```python
# Two independent timers:
- Filename poll: every 1 second
- Measurement: according to MEASUREMENT_PERIOD_S config
- CSV write: according to MEASUREMENTS_WRITE_PERIOD_S config

# Filename change handling:
1. Detect change (poll every 1s)
2. Write DataFrame to OLD file
3. Clear measurements_df
4. Start writing to NEW file

# Direct write pattern (no buffer):
- After each measurement, append directly to shared_data['measurements_df']
- Lock held only for reference assignment (microseconds)
- Immediate visibility to dashboard
```

### Dashboard Plot Updates
Enhanced live graph with all measurement traces:

**Row 1 - Active Power (kW):**
- P Setpoint (from schedule) - blue solid
- P POI (measurement) - cyan dotted
- P Battery (measurement) - green solid

**Row 2 - State of Charge (pu):**
- SoC (measurement) - purple solid

**Row 3 - Reactive Power (kvar):**
- Q Setpoint (from schedule) - orange solid
- Q POI (measurement) - cyan dotted
- Q Battery (measurement) - green solid

**Improvements:**
- Schedule plotted even without measurements
- Consistent legend order: setpoint → POI → battery

### Previous: Thread Locking Optimizations Complete
Comprehensive analysis and optimization of all thread locking patterns:

**Files Modified:**
1. **[`measurement_agent.py`](measurement_agent.py)** - HIGH PRIORITY fixes:
   - ~~Moved CSV write outside lock (was blocking during disk I/O)~~
   - ~~Implemented buffered measurement collection (flush every 10s or 100 measurements)~~
   - **SIMPLIFIED**: Direct write to shared_data (no buffer needed)
   - Lock held only for brief DataFrame reference assignment (microseconds)

2. **[`dashboard_agent.py`](dashboard_agent.py)** - HIGH PRIORITY fixes:
   - **SIMPLIFIED**: Removed local state cache and sync thread
   - Direct reads from shared_data with brief locks
   - No more 1-second stale data

3. **[`data_fetcher_agent.py`](data_fetcher_agent.py)** - LOW PRIORITY optimization:
   - Moved DataFrame `difference()` and `concat()` operations outside lock
   - Lock now only held for brief reference assignments

### Critical Bug Fix: Lock Contention Resolved (Earlier)
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

## Next Steps
- Test the simplified architecture with direct writes/reads
- Verify measurements appear immediately in dashboard (no delay)
- Verify files are created in `data/` folder with correct timestamps
- Test that Stop button stops writing and Start creates new file
- Monitor system for any lock contention issues (not expected)

## Architecture Notes

### New Data Flow (Measurement Files)
```
User clicks Start:
  Dashboard → generates filename "data/20260201_154500_data.csv"
  Dashboard → stores in shared_data['measurements_filename']
  
Measurement Agent (polling every 1s):
  Detects filename change → flushes old data → clears DataFrame → starts new file
  
User clicks Stop:
  Dashboard → sets shared_data['measurements_filename'] = None
  
Measurement Agent:
  Detects None → flushes remaining data → stops writing to disk
```

### Shared Data Structure (Updated)
```python
shared_data = {
    # Schedules
    "manual_schedule_df": pd.DataFrame(),
    "api_schedule_df": pd.DataFrame(),
    "active_schedule_source": "manual",
    
    # API configuration
    "api_password": None,
    "data_fetcher_status": {...},
    
    # Measurement file (NEW)
    "measurements_filename": None,  # Set by dashboard, polled by measurement agent
    
    # Existing data
    "measurements_df": pd.DataFrame(),
    "lock": threading.Lock(),
    "shutdown_event": threading.Event(),
}
```

### Key Files
- [`dashboard_agent.py`](dashboard_agent.py): Three-tab dashboard + filename management
- [`measurement_agent.py`](measurement_agent.py): Filename polling + measurement logging
- [`hil_scheduler.py`](hil_scheduler.py): Updated shared data with measurements_filename
- [`data/`](data/): New folder for measurement files
