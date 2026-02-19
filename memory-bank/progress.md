# Progress: HIL Scheduler

## What Works

### API Payload Hardening for Measurement Posting (2026-02-19)
- [x] **Explicit conversion pipeline preserved**: API posting still uses SoC->kWh, P->W, Q->VAr, V->V conversions.
- [x] **Conversion helper refactor**: Conversion logic moved into dedicated helpers for clearer maintenance and reduced duplication.
- [x] **One-time conversion factor validation**: `PLANT_CAPACITY_KWH` and `PLANT_POI_VOLTAGE_V` are parsed/validated once per agent startup.
- [x] **Invalid numeric payload filtering**: non-numeric, `NaN`, and `inf` values are skipped before enqueueing API payloads.
- [x] **Queue hardening**: `None` payload values are ignored, preventing bad retries and queue pollution.

**Files Modified:**
- [`measurement_agent.py`](measurement_agent.py): Added conversion helpers, finite-value filtering, and queue guards.

### API Measurement Posting Cadence + Retry Queue (2026-02-18)
- [x] **Independent API post timer**: measurement agent now posts SoC/P/Q/V on `ISTENTORE_MEASUREMENT_POST_PERIOD_S` (default 60s), decoupled from CSV write period.
- [x] **API-mode gating**: posting runs only when source is API, password is set, and `ISTENTORE_POST_MEASUREMENTS_IN_API_MODE` is true.
- [x] **Latest-sample policy**: each post tick uses the latest successful measurement sample.
- [x] **Payload conversions**:
  - SoC pu → kWh (`soc_pu * PLANT_CAPACITY_KWH`)
  - P kW → W
  - Q kvar → VAr
  - V pu → V (`v_poi_pu * PLANT_POI_VOLTAGE_V`)
- [x] **UTC ISO timestamps**: measurement payload timestamps are normalized to `YYYY-MM-DDTHH:MM:SS+00:00`.
- [x] **Bounded retry queue**:
  - configurable queue max length,
  - exponential backoff retry,
  - oldest payload dropped with warning on overflow.
- [x] **Per-series null disable**:
  - setting `measurement_series_by_plant.<plant>.<var>: null` now disables posting that variable.
- [x] **Posting API wrapper support**:
  - `istentore_api.py` now has generic `post_measurement(...)` plus typed `post_lib_*` / `post_vrfb_*` helpers.
  - 401 response triggers one re-authentication retry.

**Files Modified:**
- [`config.yaml`](config.yaml): Added posting interval/retry/series mapping settings under `istentore_api`.
- [`config_loader.py`](config_loader.py): Added flattening + validation for posting cadence, retries, queue size, and series IDs.
- [`istentore_api.py`](istentore_api.py): Added measurement posting methods and UTC timestamp normalization.
- [`measurement_agent.py`](measurement_agent.py): Added independent post scheduler, latest sample snapshot, and retry queue drain loop.

### API Midnight Rollover + Stale Setpoint Guard (2026-02-18)
- [x] **Date-aware fetcher status**: Added `today_date` and `tomorrow_date` tracking in `data_fetcher_status`.
- [x] **Deterministic rollover reconciliation**: On day change, previous fetched tomorrow is promoted to today when dates align; new tomorrow resets to pending.
- [x] **Today/tomorrow fetch lifecycle fixed**: Data fetcher no longer gets stuck with stale fetched flags after midnight.
- [x] **Configurable stale cutoff**: Added `istentore_api.schedule_period_minutes` (default 15) flattened to `ISTENTORE_SCHEDULE_PERIOD_MINUTES`.
- [x] **Scheduler stale protection (API-only)**: Scheduler dispatches zero setpoints when API row age exceeds configured validity window.
- [x] **Dashboard start stale protection**: Immediate start setpoint lookup applies same stale cutoff to avoid stale injection.
- [x] **Date-explicit API UI labels**: API tab and status row now display `Today (YYYY-MM-DD)` and `Tomorrow (YYYY-MM-DD)`.

**Files Modified:**
- [`config.yaml`](config.yaml): Added `istentore_api.schedule_period_minutes`
- [`config_loader.py`](config_loader.py): Added `ISTENTORE_SCHEDULE_PERIOD_MINUTES` flattening/validation
- [`hil_scheduler.py`](hil_scheduler.py): Added `today_date`/`tomorrow_date` to `data_fetcher_status`
- [`data_fetcher_agent.py`](data_fetcher_agent.py): Added day reconciliation and date-scoped status updates
- [`scheduler_agent.py`](scheduler_agent.py): Added API stale cutoff logic
- [`dashboard_agent.py`](dashboard_agent.py): Added API stale cutoff on Start path + date-explicit API status text

### Drift-Free Measurement Triggering (2026-02-18)
- [x] **Anchored trigger scheduling**: Measurement triggering now starts from startup time rounded up to the next whole second and follows a fixed step grid.
- [x] **Monotonic step index**: Trigger decision uses monotonic elapsed time with `floor((now_mono - anchor_mono) / measurement_period_s)`.
- [x] **Skip missed steps**: Delays no longer cause catch-up bursts; only the latest pending step is attempted.
- [x] **One attempt per step**: A step is consumed once even on Modbus read failure, preventing repeated retries within the same step.
- [x] **On-grid persisted timestamps**: Measurement rows use scheduled step timestamps rather than read-completion time.
- [x] **Recording logic preserved**: Null-boundary insertion, midnight routing, compression, and cache update behavior unchanged.

**Files Modified:**
- [`measurement_agent.py`](measurement_agent.py): Replaced elapsed-time trigger logic with anchored step scheduling and updated `take_measurement` timestamp handling.

### Timezone-Consistent Timestamp Handling (2026-02-17)
- [x] **Configurable timezone**: Added `time.timezone` in `config.yaml` and flattened `TIMEZONE_NAME` in config loader with validation/fallback.
- [x] **Shared timezone utility module**: Added `time_utils.py` for normalization and ISO serialization helpers.
- [x] **API normalization**: `istentore_api.py` now converts API UTC delivery periods to configured timezone for internal dataframe index.
- [x] **Fetcher timezone awareness**: `data_fetcher_agent.py` uses configured timezone `now` for day windows and status timestamps.
- [x] **Manual schedule normalization**: random and CSV manual schedules are normalized to configured timezone; naive timestamps treated as configured timezone.
- [x] **Scheduler lookup consistency**: scheduler uses aware current time and normalizes schedule index before `asof`.
- [x] **Measurement persistence format**: measurement CSV `timestamp` is now written as ISO 8601 with timezone offset.
- [x] **Legacy compatibility**: naive timestamps from legacy CSV/manual paths are interpreted as configured timezone.

### Daily Per-Plant Recording and Cache-Based Plot Source (2026-02-17)
- [x] **Daily per-plant filenames**: Recording now targets `data/YYYYMMDD_plantname.csv` using `modbus_local.name` / `modbus_remote.name`.
- [x] **Config flattening for plant names**: Added `PLANT_LOCAL_NAME` and `PLANT_REMOTE_NAME` in `config_loader.py`.
- [x] **Measurement agent owns persistence**: Dashboard no longer writes measurement CSVs in normal record-stop flow.
- [x] **Timestamp-routed buffers**: `pending_rows_by_file` routes rows to destination files by each row timestamp date, including midnight split handling.
- [x] **In-memory current-day file cache**: Added `current_file_path` + `current_file_df` and updated plot to read this cache directly.
- [x] **Null boundary semantics**:
  - Historical tail sanitize on record start (`latest_non_null -> append null at +period`).
  - Leading null on first real sample (`first_real - period`).
  - Trailing null on stop (`last_real + period`) with no-sample fallback.
- [x] **Midnight rollover UI path update**: Measurement agent updates `measurements_filename` to the new day file while recording.
- [x] **Plant switch compatibility maintained**: Plant switch still stops recording/clears view data to avoid mixed datasets.

**Files Modified:**
- [`config.yaml`](config.yaml): Added plant names in modbus sections
- [`config_loader.py`](config_loader.py): Added `PLANT_LOCAL_NAME` and `PLANT_REMOTE_NAME`
- [`hil_scheduler.py`](hil_scheduler.py): Added `current_file_path`, `current_file_df`, `pending_rows_by_file`
- [`measurement_agent.py`](measurement_agent.py): Reworked recording/session/persistence/cache logic
- [`dashboard_agent.py`](dashboard_agent.py): Daily filename record callback, stop-as-signal, plot source switched to `current_file_df`

### Scheduler/Recording Control Decoupling (2026-02-17)
- [x] **Independent control paths**: Start/Stop now controls only scheduler+plant operation; Record/Stop now controls only measurement recording.
- [x] **Scheduler runtime gate**: Added `shared_data['scheduler_running']` with scheduler dispatch logic gated on that state.
- [x] **Safe stop sequence**: Stop now sends zero P/Q setpoints, waits for battery measured P/Q under threshold (1.0), then disables plant (30s timeout fallback).
- [x] **Immediate Start behavior**: Start enables plant and sends current schedule setpoint immediately.
- [x] **Schedule switch behavior updated**: Schedule source switching safe-stops plant but does not flush/clear measurement data.
- [x] **Plant switch behavior preserved**: Plant switching safe-stops plus flushes/clears measurements to avoid mixed datasets.
- [x] **Recording UX added**: New `Record` and `Stop` buttons in the plot card with recording status text and stop-button enable/disable behavior.
- [x] **Recording control decoupled**: Record/Stop control recording state independent of scheduler Start/Stop.

**Files Modified:**
- [`hil_scheduler.py`](hil_scheduler.py): Added `scheduler_running` to shared_data
- [`scheduler_agent.py`](scheduler_agent.py): Added scheduler dispatch gating and restart cache reset behavior
- [`dashboard_agent.py`](dashboard_agent.py): Added safe stop helpers, decoupled callbacks, and new recording controls
- [`assets/custom.css`](assets/custom.css): Added styles for recording controls and responsive layout

### Dashboard Initial State Loading Fix (2026-02-02)
- [x] **Changed to prevent_initial_call='initial_duplicate'**: Plant and schedule source selection callbacks now execute on initial load while supporting duplicate outputs
- [x] **Removed hardcoded values**: RadioItems no longer have hardcoded `value='local'` or `value='manual'`
- [x] **Fixed callback return values**: Both callbacks now return all 5 values including `current_system_status`
- [x] **Initial state from shared_data**: Dashboard now correctly reads `selected_plant` and `active_schedule_source` from shared_data on startup
- [x] **Config-driven startup**: Dashboard reflects `startup.plant` and `startup.schedule_source` from config.yaml

**Files Modified:**
- [`dashboard_agent.py`](dashboard_agent.py): 
  - Changed `prevent_initial_call=True` to `prevent_initial_call='initial_duplicate'` in `select_plant` and `select_active_source` callbacks
  - Removed hardcoded `value='local'` from `selected-plant-selector` RadioItems
  - Removed hardcoded `value='manual'` from `active-source-selector` RadioItems
  - Fixed `select_plant` callback to return 5 values including `current_system_status`
  - Fixed `select_active_source` callback to return 5 values including `current_system_status`

**Behavior:**
- When `startup.plant: "remote"` in config.yaml, dashboard shows "Remote" on startup
- When `startup.schedule_source: "api"` in config.yaml, dashboard shows "API" on startup
- User can still switch plants/schedules via UI with confirmation modal

**Technical Note:**
- `prevent_initial_call='initial_duplicate'` is required because callbacks have `allow_duplicate=True` outputs (for `system-status` store)
- Dash requires either `prevent_initial_call=True` or `prevent_initial_call='initial_duplicate'` when using `allow_duplicate=True`
- `initial_duplicate` allows callbacks to run on initial page load while still supporting duplicate outputs
- Hardcoded `value` attributes in RadioItems were overriding callback outputs, preventing proper initialization
- Callbacks must return exactly 5 values to match the output schema (selector value, button classes, modal class, system-status)

### Log File Selector Implementation (2026-02-02)
- [x] **Dropdown Selector**: Replaced "Clear Display" button with log file dropdown
- [x] **File Scanning**: Automatic scanning of `logs/` folder for `.log` files
- [x] **Historical Log Display**: Color-coded parsing and display of historical log files
- [x] **Auto-refresh Control**: Disabled auto-refresh when viewing historical files
- [x] **Regex Pattern Fix**: Fixed regex to match actual log format without milliseconds
- [x] **File Sorting**: Log files sorted by date (newest first)
- [x] **Error Handling**: Graceful handling of file read errors and malformed lines
- [x] **Current Session Option**: "Current Session" option for live logs with auto-refresh

**Files Modified:**
- [`dashboard_agent.py`](dashboard_agent.py): Added log file selector dropdown, file scanning, historical log parsing with color coding, auto-refresh control logic

### Dashboard State Transition Improvements (2026-02-02)
- [x] **Config-Based Timing**: Dashboard refresh uses `measurement_period_s` from config (1s)
- [x] **Frequent Status Checks**: Status check runs every interval instead of every 6 seconds
- [x] **Intermediate States**: "Starting..." and "Stopping..." states with animated badges
- [x] **Instant Feedback**: Status updates immediately when Start/Stop buttons clicked
- [x] **Transition Logic**: Automatically transitions to final state when Modbus confirms
- [x] **Button Protection**: Both buttons disabled during transitions
- [x] **CSS Animations**: Pulse animation for transition states (blue=starting, purple=stopping)

**Files Modified:**
- [`dashboard_agent.py`](dashboard_agent.py): Added intermediate state logic, config-based timing
- [`assets/custom.css`](assets/custom.css): Added `.status-badge-starting`, `.status-badge-stopping`, `@keyframes pulse`

### Enhanced Logging System (2026-02-02)
- [x] **Daily Log Files**: Rotating log files in `logs/` folder with daily rotation
- [x] **File Format**: `logs/hil_scheduler_YYYY-MM-DD.log`
- [x] **30-Day Retention**: Configurable backup count for old log files
- [x] **Auto-Creation**: logs/ directory created automatically if missing
- [x] **Three Outputs**: Console, file, and dashboard session logs
- [x] **Session Log Handler**: Custom handler captures logs to shared_data
- [x] **Logs Tab in Dashboard**: New 4th tab displaying real-time session logs
- [x] **Color Coding**: ERROR=red, WARNING=orange, INFO=green, DEBUG=gray
- [x] **Auto-Refresh**: Dashboard logs update every 2 seconds
- [x] **Clear Display Button**: Clears dashboard view (not file logs)
- [x] **Memory Protection**: Max 1000 entries in session buffer
- [x] **Log File Path Display**: Shows current log file location in dashboard
- [x] **Git Ignore**: logs/ directory added to .gitignore

**Files:**
- [`logger_config.py`](logger_config.py): Logger configuration module with SessionLogHandler
- [`hil_scheduler.py`](hil_scheduler.py): Updated to use new logging setup
- [`dashboard_agent.py`](dashboard_agent.py): Added Logs tab and display callbacks
- [`.gitignore`](.gitignore): Added logs/ directory

### Schedule Switch Confirmation (2026-02-02)
- [x] **Confirmation Modal**: Modal appears when switching between Manual and API schedules
- [x] **Stop System on Switch**: System is stopped via Modbus before switching
- [x] **Flush Measurements**: Measurements flushed to CSV file before switching
- [x] **Clear DataFrame**: Measurements DataFrame cleared after flushing
- [x] **Plant Switch Updated**: Now also flushes and clears measurements (same as schedule switch)
- [x] **API Connection Preserved**: API connection remains active during schedule switches
- [x] **Schedule Data Preserved**: Both manual and API schedules remain intact
- [x] **Helper Functions**: `stop_system()` and `flush_and_clear_measurements()` available for reuse

### Dual Plant Support (2026-02-01)
- [x] **Local Plant**: Emulated plant running in `plant_agent.py` (localhost:5020)
- [x] **Remote Plant**: Real hardware plant (10.117.133.21:502)
- [x] **Dynamic Switching**: Scheduler and Measurement agents auto-reconnect on plant change
- [x] **Dashboard UI**: Plant selector with Local/Remote options in Status & Plots tab
- [x] **Modal Confirmation**: Confirmation dialog before switching plants
- [x] **Safe Switching**: Current plant stopped before switching, user manually starts new plant
- [x] **Startup Configuration**: `startup.plant` config sets initial plant on application start
- [x] **Modbus Config Separation**: `modbus_local` and `modbus_remote` sections in config.yaml

### Configuration Cleanup (2026-02-01)
- [x] Removed unused `MEASUREMENTS_CSV` from config.yaml
- [x] Removed unused `MEASUREMENTS_CSV` from config.py
- [x] Removed unused `MEASUREMENTS_CSV` from config_loader.py
- [x] Removed unused `MEASUREMENTS_CSV` from dashboard_agent.py
- [x] Renamed `p_battery_actual` → `p_battery` in config.yaml
- [x] Renamed `q_battery_actual` → `q_battery` in config.yaml
- [x] Updated config_loader.py to use new names
- [x] Updated memory bank documentation

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
- [x] **Daily per-plant filenames**: `data/YYYYMMDD_plantname.csv`
- [x] **Filename in Shared Data**: `measurements_filename` control field
- [x] **Record Button**: Sets selected-plant current-day path
- [x] **Record Stop Button**: Clears filename (`None`)
- [x] **Buffered Writes**: Agent flushes `pending_rows_by_file` every `measurements_write_period_s`
- [x] **Row Timestamp Routing**: Midnight split handled by routing each row to its date file
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
- [x] **Tab 3: Status & Plots** - Unified control card with toggles, live graphs, system status
- [x] Modern professional light theme with clean white surfaces
- [x] Schedule preview with diff visualization (existing vs preview)
- [x] Responsive design with CSS media queries

### Two-Row Control Panel (2026-02-01)
- [x] Two-row compact control panel replacing scattered cards
- [x] **Row 1**: Start/Stop buttons, Schedule toggle, Plant toggle (1/3 width each on medium+)
- [x] **Row 2**: Status badge + messages (Source, API, Last update)
- [x] Full-width buttons and toggles on small screens (≤640px)
- [x] Status messages flow with wrapping on small screens
- [x] Responsive design with 2 breakpoints

**CSS Classes Added:**
- `.control-panel` - Main container with two rows
- `.controls-row` - Row 1 with 3-column layout
- `.control-section` - Each 1/3 section (flex: 1)
- `.control-group` - Start/Stop button container
- `.control-btn` - Compact buttons (flex: 1 on small screens)
- `.toggle-wrapper` - Label + toggle container
- `.compact-toggle` - Horizontal toggle switch (flex: 1 on small screens)
- `.toggle-option` - Individual toggle button
- `.status-row` - Row 2 with flowing status messages
- `.status-badge` - Compact pill-style status indicator
- `.status-text` - Status message text

**Layout Structure:**
```
Row 1 (Controls):
┌─────────────┬──────────────┬──────────────┐
│ ▶Start ■Stop│ Schedule:[M A]│ Plant:[L R]  │
└─────────────┴──────────────┴──────────────┘

Row 2 (Status):
● Running | Source: Manual | API: Connected | 21:30
```

**Responsive Breakpoints:**
- Desktop (>768px): 3-column controls, status in single row
- Tablet (≤768px): Same layout, smaller elements  
- Mobile (≤640px): Full-width stacked controls, flowing status with wrap

**Key Features:**
- Plant switch confirmation modal preserved
- Status messages restored (Source, API status, Last update)
- Clean visual hierarchy with uppercase labels
- All controls stretch to full width on mobile
- Status messages wrap naturally on small screens

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
Scheduler and recording control paths are decoupled with safe-stop logic and dedicated recording controls.

### Code Quality
- Scheduler dispatch is explicitly gated by `scheduler_running`
- Safe stop sequence uses measured battery power threshold checks with timeout fallback
- Recording rotation/stop logic is isolated from plant enable/disable control

### Documentation Status
- [x] Memory Bank initialized with core files
- [x] Legacy docs removed
- [x] Plan documents created for major features
- [x] activeContext.md updated with current focus

## Recent Changes (2026-02-01)

### Configuration Cleanup
Removed unused configuration and simplified register names:

**Removed MEASUREMENTS_CSV:**
- Static config no longer needed with dynamic filename system
- Files modified: `config.yaml`, `config.py`, `config_loader.py`, `dashboard_agent.py`

**Renamed Registers:**
- `p_battery_actual` → `p_battery`
- `q_battery_actual` → `q_battery`
- Config dictionary keys remain unchanged for backward compatibility

### Register Naming Cleanup
Renamed Modbus register names in YAML configuration for consistency:

**Changes:**
- `p_battery_actual` → `p_battery` 
- `q_battery_actual` → `q_battery`

**Files Modified:**
- `config.yaml`: Updated register names
- `config_loader.py`: Updated to read new names
- `memory-bank/systemPatterns.md`: Updated register map table

Note: Config dictionary keys (`PLANT_P_BATTERY_ACTUAL_REGISTER`, `PLANT_Q_BATTERY_ACTUAL_REGISTER`) remain unchanged for backward compatibility.

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
Implemented daily per-plant measurement file handling:

**Files Modified:**
1. `hil_scheduler.py`: Added `measurements_filename` to shared_data
2. `dashboard_agent.py`: Record sets daily per-plant filename, Stop clears it
3. `measurement_agent.py`: Complete rewrite with timestamp-routed buffering + cache

**Features:**
- Daily per-plant filenames: `data/YYYYMMDD_plantname.csv`
- Filename stored in shared_data
- Null-boundary rows on record start/first-sample/stop
- Midnight-safe split by row timestamp
- In-memory `current_file_df` includes pending unflushed rows for plotting

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
