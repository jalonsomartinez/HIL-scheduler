# Active Context: HIL Scheduler

## Current Focus
Dual plant support implementation complete. Application now supports switching between local (emulated) and remote (real hardware) plants via dashboard UI with modal confirmation.

## Recent Changes (2026-02-01) - Dual Plant Support

### Overview
Implemented support for communicating with two different plants:
1. **Local Plant**: Emulated plant running in `plant_agent.py` (localhost:5020)
2. **Remote Plant**: Real hardware plant (10.117.133.21:502)

### Files Modified

**1. Configuration Changes:**
- [`config.yaml`](config.yaml): 
  - Renamed `modbus` → `modbus_local`
  - Added `modbus_remote` section with same register structure
  - Added `startup` section for initial schedule_source and plant selection
  - Merged schedule settings into unified `schedule` section
- [`config_loader.py`](config_loader.py): Loads both plant configurations
- [`config.py`](config.py): Legacy config retained for reference

**2. Core Agent Updates:**
- [`hil_scheduler.py`](hil_scheduler.py): 
  - Added `selected_plant` to shared_data (default from config)
  - Added `plant_switching` flag for coordination
- [`scheduler_agent.py`](scheduler_agent.py): 
  - Dynamic plant switching based on `selected_plant`
  - Reconnects to appropriate Modbus host/port when changed
- [`measurement_agent.py`](measurement_agent.py):
  - Same dynamic switching logic as scheduler
  - All register reads use plant-specific configuration

**3. Dashboard Updates:**
- [`dashboard_agent.py`](dashboard_agent.py):
  - New "Plant Selection" UI in Status & Plots tab
  - Modal confirmation dialog before switching plants
  - Plant switching sequence: Stop → Update shared_data → Wait for user Start
  - Start/Stop buttons control the currently selected plant

### Plant Switching Flow
1. User clicks to change plant in dashboard
2. Modal asks for confirmation
3. On confirm: sends STOP to current plant
4. Updates `selected_plant` in shared_data
5. Scheduler and Measurement agents automatically reconnect
6. User manually clicks Start on new plant

### Startup Configuration
New `startup` section in config.yaml:
```yaml
startup:
  schedule_source: "manual"    # Options: "manual" or "api"
  plant: "local"               # Options: "local" or "remote"
```

### Configuration Cleanup
- Removed unused schedule power limit configs (now using plant.power_limits)
- Removed unused default_* configs (not referenced anywhere)
- Unified schedule settings into single `schedule` section

---

## Previous: Simplified data fetcher timing strategy. Now uses single polling interval from config with unified error backoff.

## Recent Changes (2026-02-01) - Configuration Cleanup

### Removed Unused MEASUREMENTS_CSV Config
The static `MEASUREMENTS_CSV` configuration was no longer needed since implementing dynamic filename generation:

**Files Modified:**
- [`config.yaml`](config.yaml): Removed `output:` section with `measurements_csv`
- [`config.py`](config.py): Removed `MEASUREMENTS_CSV` from both remote and local configs
- [`config_loader.py`](config_loader.py): Removed output section parsing
- [`dashboard_agent.py`](dashboard_agent.py): Removed from test config

**Why:** The dashboard now generates timestamped filenames (e.g., `data/20260201_154500_data.csv`) and stores them in `shared_data['measurements_filename']`. The measurement agent polls this value rather than using a static config.

---

### Renamed YAML Register Names
Simplified register names in [`config.yaml`](config.yaml):
- `p_battery_actual` → `p_battery`
- `q_battery_actual` → `q_battery`

**Files Modified:**
- [`config.yaml`](config.yaml): Updated register names in modbus.registers section
- [`config_loader.py`](config_loader.py): Updated to read new register names
- [`memory-bank/systemPatterns.md`](memory-bank/systemPatterns.md): Updated register map table

The config keys `PLANT_P_BATTERY_ACTUAL_REGISTER` and `PLANT_Q_BATTERY_ACTUAL_REGISTER` remain unchanged in the flat config dictionary for backward compatibility.

---

## Recent Changes (2026-02-01) - Data Fetcher Timing Simplification

### Problem Identified
The data fetcher had multiple hardcoded sleep times that overrode config values:
- 5s when no password set
- 30s on authentication error  
- 300s after first fetch (hardcoded, ignored config)
- 60s before first fetch (from config)
- 5s on unexpected error
- Unused config: `ISTENTORE_POLL_INTERVAL_MIN: 10` (never referenced in code)

### Solution Implemented
**Simplified to two timing values:**
1. **Normal polling**: Uses `DATA_FETCHER_PERIOD_S` from config (default: 120s)
2. **Error backoff**: Single hardcoded value of 30s for all error conditions

**Files Modified:**
1. **[`data_fetcher_agent.py`](data_fetcher_agent.py)**:
   - Replaced multiple hardcoded sleeps with `poll_interval_s` variable
   - Added `error_backoff_s = 30` constant for all errors
   - Removed conditional logic that changed sleep time based on first_fetch_done
   - Added startup logging showing timing configuration
   - Updated docstring with timing strategy documentation

2. **[`config.yaml`](config.yaml)**:
   - Removed unused `poll_interval_min: 10` 
   - Kept `poll_start_time: "17:30"` (still used for tomorrow's schedule timing)

3. **[`config.py`](config.py)**:
   - Changed `DATA_FETCHER_PERIOD_S` from 1s to 120s (matches config.yaml)

**New Timing Behavior:**
| Condition | Sleep Time | Source |
|-----------|------------|--------|
| Normal operation | 120s | Config (`DATA_FETCHER_PERIOD_S`) |
| No password | 30s | Hardcoded error backoff |
| Auth error | 30s | Hardcoded error backoff |
| Fetch error | 30s | Hardcoded error backoff |
| Unexpected error | 30s | Hardcoded error backoff |

**Benefits:**
- Predictable timing behavior
- Single source of truth for polling interval
- Simpler code (removed first_fetch_done tracking for timing)
- Clear separation: config for normal, hardcoded for errors

---

## Previous: Eliminated Buffer and Local State

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
