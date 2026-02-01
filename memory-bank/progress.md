# Progress: HIL Scheduler

## What Works

### Data Fetcher Timing Simplification (2026-02-01)
- [x] Single polling interval from config (`DATA_FETCHER_PERIOD_S: 120`)
- [x] Single error backoff (30s hardcoded for all error conditions)
- [x] Removed unused `ISTENTORE_POLL_INTERVAL_MIN` config
- [x] Consistent timing behavior (no more 300s hardcoded override)
- [x] Startup logging shows timing configuration

### Core Agents (New Architecture)
- [x] **Director Agent** (`hil_scheduler.py`): Updated shared data structure with two schedules + measurements_filename
- [x] **Data Fetcher Agent** (`data_fetcher_agent.py`): **REWRITTEN** - Decoupled API-only fetcher
- [x] **Scheduler Agent** (`scheduler_agent.py`): **UPDATED** - Reads active_schedule_source to choose schedule
- [x] **Plant Agent** (`plant_agent.py`): Merged PPC + Battery functionality (unchanged)
- [x] **Measurement Agent** (`measurement_agent.py`): **REWRITTEN** - Filename polling + dynamic file management
- [x] **Dashboard Agent** (`dashboard_agent.py`): **REWRITTEN** - Three-tab structure + filename generation

### New Architecture (2026-02-01)
- [x] **Two Shared Schedules**: `manual_schedule_df` and `api_schedule_df`
- [x] **Active Source Selector**: `active_schedule_source` ('manual' or 'api')
- [x] **Decoupled Data Fetcher**: No polling, just fetches API when password is set
- [x] **Manual Schedule Manager** (`manual_schedule_manager.py`): Simple utility module

### Measurement File Management (2026-02-01)
- [x] **Dynamic Filenames**: `data/YYYYMMDD_HHMMSS_data.csv` format
- [x] **Filename in Shared Data**: `measurements_filename` field
- [x] **Start Button**: Generates new timestamped filename
- [x] **Stop Button**: Clears filename (sets to None)
- [x] **Filename Polling**: Agent checks every 1 second for changes
- [x] **Automatic File Rotation**: Flush old, clear DataFrame, start new
- [x] **Data Folder**: All files stored in `data/` subdirectory

### Dashboard Plots (2026-02-01)
- [x] **Row 1 - Active Power**: P Setpoint, P POI, P Battery
- [x] **Row 2 - State of Charge**: SoC
- [x] **Row 3 - Reactive Power**: Q Setpoint, Q POI, Q Battery
- [x] **Schedule Always Plotted**: Even without measurement data
- [x] **Consistent Legend Order**: setpoint → POI → battery

### Schedule Management
- [x] **Manual Schedule**: Random generation and CSV upload via dashboard
- [x] **API Schedule**: Fetched independently by Data Fetcher agent
- [x] **Smart Replacement**: New data replaces only overlapping periods
- [x] **asof() Lookup**: Scheduler uses pandas asof() for robust time-based lookup

### Dashboard UI (Three-Tab Structure)
- [x] **Tab 1: Manual Schedule** - Random generation, CSV upload, preview/accept
- [x] **Tab 2: API Schedule** - Password input, connection status, API schedule preview
- [x] **Tab 3: Status & Plots** - Active source selector, live graphs, system status
- [x] Modern professional light theme with clean white surfaces
- [x] Schedule preview with diff visualization (existing vs preview)
- [x] Responsive design with CSS media queries

## What's Left to Build

### Potential Improvements (Not Yet Planned)
- [ ] Command-line argument parsing for runtime configuration
- [ ] More sophisticated schedule generation (not just random)
- [ ] Historical data analysis tools
- [ ] Better error recovery and retry mechanisms
- [ ] Unit tests for individual agents
- [ ] Integration tests for full workflow
- [ ] Docker containerization for easy deployment
- [ ] API endpoint for external schedule submission

## Current Status

### Project Phase
Measurement file management system completed. Dashboard plots enhanced with all traces.

### Code Quality
- Measurement agent uses three independent timers (filename, measurement, CSV write)
- Thread-safe filename change detection
- All 7 measurement traces plotted in consistent order

### Documentation Status
- [x] Memory Bank initialized with core files
- [x] Legacy docs removed
- [x] Plan documents created for major features
- [x] activeContext.md updated with current focus

## Recent Changes (2026-02-01)

### Data Fetcher Timing Simplification
Simplified data fetcher timing to use single config value with unified error backoff:

**Problem:**
- Multiple hardcoded sleep times (5s, 30s, 300s) overrode config values
- `ISTENTORE_POLL_INTERVAL_MIN: 10` config was defined but never used
- Timing logic was complex: different sleeps for password state, auth errors, first fetch status

**Solution:**
- Normal polling: Uses `DATA_FETCHER_PERIOD_S` from config (120s)
- All errors: Use hardcoded 30s backoff
- Removed unused config value
- Added startup logging for timing transparency

**Files Modified:**
- `data_fetcher_agent.py`: Replaced complex timing with simple two-value approach
- `config.yaml`: Removed `poll_interval_min`
- `config.py`: Updated `DATA_FETCHER_PERIOD_S` from 1s to 120s

### Eliminated Buffer and Local State (Simplified Architecture)
Removed unnecessary buffering and local state caching to reduce latency:

**Rationale:**
- Locks are only held for microseconds (reference assignments)
- The buffer added up to 1s delay before measurements appeared
- The local state added another 1s delay before dashboard saw updates
- Total latency reduction: up to 2 seconds

**Files Modified:**
1. `measurement_agent.py`:
   - Removed `measurement_buffer` list
   - Removed `flush_buffer_to_dataframe()` function
   - Removed `FLUSH_INTERVAL_S` and `BUFFER_SIZE_LIMIT` constants
   - Now writes directly to `shared_data['measurements_df']` after each measurement
   
2. `dashboard_agent.py`:
   - Removed `local_state` dictionary
   - Removed `sync_from_shared_data()` background thread
   - Removed `sync_thread`
   - Callbacks now read directly from `shared_data` with brief locks
   - Only `last_modbus_status` remains as mutable state

**Benefits:**
- Reduced latency: measurements appear immediately in dashboard
- Simpler code: ~50 lines removed from each agent
- No background threads needed for data sync
- Data freshness: dashboard always sees current state

### Measurement File Management System
Implemented dynamic measurement file handling:

**Files Modified:**
1. `hil_scheduler.py`: Added `measurements_filename` to shared_data
2. `dashboard_agent.py`: Start generates timestamped filename, Stop clears it
3. `measurement_agent.py`: Complete rewrite with filename polling

**Features:**
- Timestamped filenames: `data/YYYYMMDD_HHMMSS_data.csv`
- Filename stored in shared_data
- Poll every 1 second for changes
- Automatic file rotation on new Start
- Stop clears filename (sets to None)

### Dashboard Plot Enhancements
Enhanced live graph with all measurement traces:

**Active Power (kW):**
- P Setpoint (schedule) - blue solid
- P POI (measurement) - cyan dotted
- P Battery (measurement) - green solid

**State of Charge (pu):**
- SoC (measurement) - purple solid

**Reactive Power (kvar):**
- Q Setpoint (schedule) - orange solid
- Q POI (measurement) - cyan dotted
- Q Battery (measurement) - green solid

### Thread Locking Optimizations Implemented
Comprehensive analysis and optimization of all thread locking patterns:

**Analysis Document:** [`plans/thread_locking_analysis.md`](plans/thread_locking_analysis.md)

**Optimizations Applied:**
1. **measurement_agent.py** (HIGH PRIORITY):
   - ~~Moved CSV `to_csv()` outside lock - prevents disk I/O blocking~~
   - ~~Implemented buffered measurement collection (flush every 10s or 100 measurements)~~
   - **SIMPLIFIED**: Direct write to shared_data, no buffer needed (locks are microseconds)
   - Lock held only for DataFrame reference assignment

2. **dashboard_agent.py** (HIGH PRIORITY):
   - **SIMPLIFIED**: Removed local state cache and sync thread
   - Direct reads from shared_data with brief locks
   - No more 1-second stale data

3. **data_fetcher_agent.py** (LOW PRIORITY):
   - Moved DataFrame `difference()` and `concat()` operations outside lock
   - Lock only held for brief reference assignments

**Lock Safety Patterns Documented:**
- ~~Measurement buffer pattern for high-frequency updates~~
- Direct access pattern: brief locks for immediate data freshness
- CSV write pattern (copy outside lock, never I/O in lock)
- DataFrame merge pattern (prepare outside, assign briefly)

### Critical Bug Fix: Dashboard UI Freezing Resolved
**Issue:** Dashboard showed "Updating" status for long periods, tab switching was slow.

**Root Cause:** `scheduler_agent.py` held shared data lock during slow operations (asof lookup, Modbus writes).

**Fix:** Refactored scheduler_agent.py to minimize lock time:
- Lock only held for dictionary lookups (microseconds)
- All DataFrame operations and Modbus writes happen outside lock
- Safe because DataFrames are read-only from scheduler perspective

**Verification:** Dashboard is now responsive with no "Updating" delays.

### Major Architecture Refactoring
Split schedule management into two independent schedules:

**New Components:**
- `manual_schedule_df` - Managed directly by dashboard
- `api_schedule_df` - Managed by decoupled Data Fetcher agent
- `active_schedule_source` - Selects which schedule the scheduler uses
- `manual_schedule_manager.py` - Simple utility for random/CSV operations

**Rewritten Agents:**
- **Data Fetcher**: Simple loop, no mode polling, just fetches API when password set
- **Dashboard**: Three-tab structure (Manual, API, Status & Plots)
- **Scheduler**: Reads `active_schedule_source` to choose schedule

### Schedule Creation Simplification (Earlier)
- Merged Random Schedule and CSV Upload into single "Manual" mode
- Mode selector now shows 2 options: Manual | API

## Previous Changes (2026-01-31)

### Dashboard UI Redesign
- Complete UI overhaul with modern professional light theme
- Two tabs: Schedule Configuration and Status & Plots
- Preview workflow with diff visualization
- Accept/Clear buttons for schedule changes
- Fixed duplicate callback outputs error

### Preview Workflow Implementation
- Manual → Random: Configure start/end/step → Preview → Accept
- Manual → CSV: Upload file → Adjust start date/time → Preview updates → Accept
- API Mode: Enter password → Connect & Fetch
- Diff visualization: Existing (dashed gray) vs Preview (solid blue fill)

### CSS Styling
- Color palette: Blue (#2563eb), Green (#16a34a), Red (#dc2626)
- Uniform spacing scale (4px, 8px, 12px, 16px, 24px)
- Standardized border radius (6px-8px)
- Responsive breakpoints for mobile devices
