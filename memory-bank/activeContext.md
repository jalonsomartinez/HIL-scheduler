# Active Context: HIL Scheduler

## Current Focus
Daily per-plant recording files with measurement-agent-owned persistence and in-memory current-day file caching for plotting.

## Recent Changes (2026-02-17) - Daily Per-Plant Recording and Cache-Based Plot Source

### Overview
Refactored recording to use one file per plant per day (`data/YYYYMMDD_plantname.csv`) and moved all normal CSV persistence ownership to `measurement_agent.py`.

### Key Behavior Changes
- `Record` no longer creates a timestamped file per click; it targets the selected plant/day file.
- `Stop` no longer writes CSV from dashboard; it only signals stop and measurement agent appends trailing null + flushes.
- Plot source now uses in-memory `shared_data['current_file_df']` (selected plant current-day file + pending unflushed rows), not repeated CSV reads.
- Measurement rows are buffered by target file path (derived from each row timestamp), so midnight splits are naturally correct.

### Null-Boundary Semantics Implemented
- On recording start:
  - Historical tail is sanitized for that plant: if latest historical row is non-null, append a null row at `latest_ts + measurement_period`.
  - Session waits for first real sample.
- On first real sample of session:
  - Adds leading null row at `first_real_ts - measurement_period`, then the real sample.
- On recording stop:
  - Adds trailing null row at `last_real_ts + measurement_period`.
  - If no real sample occurred, adds fallback null at stop time.

### Files Modified
1. **[`config.yaml`](config.yaml)**:
   - Added `modbus_local.name` and `modbus_remote.name`.

2. **[`config_loader.py`](config_loader.py)**:
   - Added flattened config keys `PLANT_LOCAL_NAME` and `PLANT_REMOTE_NAME` with local/remote fallbacks.

3. **[`hil_scheduler.py`](hil_scheduler.py)**:
   - Added shared runtime keys:
     - `current_file_path`
     - `current_file_df`
     - `pending_rows_by_file`

4. **[`measurement_agent.py`](measurement_agent.py)**:
   - Reworked to:
     - own CSV persistence for recording,
     - route rows to files by timestamp date,
     - maintain selected-plant/day in-memory cache,
     - apply null-boundary and historical sanitize logic,
     - update active recording filename on midnight rollover.

5. **[`dashboard_agent.py`](dashboard_agent.py)**:
   - Record button now sets daily per-plant filename.
   - Recording stop no longer writes CSV directly.
   - Plot callback now reads `current_file_df`.

## Recent Changes (2026-02-17) - Scheduler/Recording Decoupling

### Overview
Implemented the dashboard behavior split:
- `Start/Stop` now controls only scheduler + plant operation.
- New `Record/Stop` controls measurement file recording independently.
- Schedule source switches now stop the plant safely but preserve measurements.
- Plant switches keep safe stop + flush/clear behavior to avoid mixing local/remote datasets.

### Files Modified
1. **[`hil_scheduler.py`](hil_scheduler.py)**:
   - Added `scheduler_running` to `shared_data` (default `False`)

2. **[`scheduler_agent.py`](scheduler_agent.py)**:
   - Added scheduler dispatch gating on `shared_data['scheduler_running']`
   - Scheduler now sends no setpoints while paused
   - Resets cached previous setpoints while paused so Start sends immediately on resume

3. **[`dashboard_agent.py`](dashboard_agent.py)**:
   - Added helper functions:
     - `get_selected_plant_modbus_config()`
     - `set_enable()`
     - `send_setpoints()`
     - `wait_until_battery_power_below_threshold()`
     - `safe_stop_plant()`
     - `stop_recording_and_flush()`
   - Updated Start/Stop callback semantics:
     - Start: sets `scheduler_running=True`, enables plant, sends latest setpoint immediately
     - Stop: runs safe-stop sequence (zero setpoints → wait below 1kW/kvar threshold → disable)
   - Added new recording controls in plot card:
     - `record-button`
     - `record-stop-button`
     - recording status text
   - Added recording callback for file start/rotation/stop behavior
   - Updated schedule switch flow to safe-stop only (no measurement flush)
   - Kept plant switch flow as safe-stop + flush/clear

4. **[`assets/custom.css`](assets/custom.css)**:
   - Added styles for record controls row and responsive behavior

### Behavior Notes
- Safe stop timeout default is 30 seconds; plant is force-disabled on timeout with warning log.
- Safe stop threshold checks battery measured active and reactive power (`abs(P_batt)<1`, `abs(Q_batt)<1`).
- Record button targets the daily per-plant file; repeated clicks on the same day/plant keep the same file path.

## Recent Changes (2026-02-02) - Dashboard Initial State Loading Fix

## Recent Changes (2026-02-02) - Dashboard Initial State Loading Fix

### Overview
Fixed dashboard not reflecting startup configuration for plant and schedule source selection.

**Problem:** When starting the app with `startup.plant: "remote"` in config.yaml, the dashboard showed "local" instead. Similarly, `startup.schedule_source: "api"` was not reflected.

**Root Cause:** The `select_plant` and `select_active_source` callbacks had `prevent_initial_call=True`, which prevented them from running on initial page load. The `if not ctx.triggered:` block meant to handle initial load never executed.

**Solution:** Changed `prevent_initial_call=True` to `prevent_initial_call='initial_duplicate'` in both callbacks. This allows callbacks to run on initial load while still supporting duplicate outputs (required for `system-status` store).

### Files Modified

1. **[`dashboard_agent.py`](dashboard_agent.py)**:
   - Changed `prevent_initial_call=True` to `prevent_initial_call='initial_duplicate'` in `select_plant` callback (line 972)
   - Changed `prevent_initial_call=True` to `prevent_initial_call='initial_duplicate'` in `select_active_source` callback (line 869)
   - Removed hardcoded `value='local'` from `selected-plant-selector` RadioItems (line 157)
   - Removed hardcoded `value='manual'` from `active-source-selector` RadioItems (line 148)
   - Fixed `select_plant` callback to return 5 values including `current_system_status` (line 983, 992)
   - Fixed `select_active_source` callback to return 5 values including `current_system_status` (line 880, 891)
   - Both callbacks now execute on initial load and read from `shared_data`
   - RadioItems no longer have hardcoded initial values

### Technical Details

**Before Fix:**
```python
@app.callback(
    [...],
    [...],
    prevent_initial_call=True  # Prevents callback on initial load
)
def select_plant(...):
    if not ctx.triggered:
        # This block never executes due to prevent_initial_call=True
        with shared_data['lock']:
            stored_plant = shared_data.get('selected_plant', 'local')
        return ...
```

**After Fix:**
```python
@app.callback(
    [...],
    [...],
    prevent_initial_call='initial_duplicate'  # Allows initial call, supports duplicate outputs
)
def select_plant(...):
    if not ctx.triggered:
        # This block now executes on initial load
        with shared_data['lock']:
            stored_plant = shared_data.get('selected_plant', 'local')
        return ...
```

**Why `initial_duplicate`:**
- The callbacks have `allow_duplicate=True` outputs (e.g., `Output('system-status', 'data', allow_duplicate=True)`)
- Dash requires `prevent_initial_call=True` or `prevent_initial_call='initial_duplicate'` when using `allow_duplicate=True`
- `initial_duplicate` allows the callback to run on initial page load while still supporting duplicate outputs

### Behavior After Fix
- Dashboard now correctly shows "Remote" when `startup.plant: "remote"` in config.yaml
- Dashboard now correctly shows "API" when `startup.schedule_source: "api"` in config.yaml
- Initial state is loaded from `shared_data['selected_plant']` and `shared_data['active_schedule_source']`
- User can still switch plants/schedules via UI with confirmation modal

---

## Previous Focus (2026-02-02) - 16-bit Modbus Register Unification

### Overview
Changed all power register operations from 32-bit to 16-bit to match the remote plant configuration.

**Problem:** Code used pyModbusTCP's `long_list_to_word()` / `word_list_to_long()` for 32-bit values, but remote plant uses 16-bit registers.

**Solution:** Added new utility functions for 16-bit signed handling and updated all agents to use single-register reads/writes.

### Files Modified

1. **[`utils.py`](utils.py)**:
   - Added `int_to_uint16()` - Convert signed int to unsigned 16-bit register value
   - Added `uint16_to_int()` - Convert unsigned 16-bit register value to signed int

2. **[`config.yaml`](config.yaml)**:
   - Updated comments in `modbus_local` section to show "16-bit signed" for power registers

3. **[`scheduler_agent.py`](scheduler_agent.py)**:
   - Replaced `long_list_to_word()` import with `int_to_uint16`
   - Changed `write_multiple_registers()` to `write_single_register()`
   - Setpoints now write as single 16-bit values

4. **[`measurement_agent.py`](measurement_agent.py)**:
   - Removed `get_2comp` and `word_list_to_long` imports, added `uint16_to_int`
   - Changed `read_holding_registers(..., 2)` to `read_holding_registers(..., 1)` for power values
   - Decodes using `uint16_to_int()` instead of 32-bit conversion

5. **[`plant_agent.py`](plant_agent.py)**:
   - Removed 32-bit utility imports, added `int_to_uint16` and `uint16_to_int`
   - Server initialization uses single-value lists instead of `long_list_to_word()`
   - Reading/writing registers now uses 1 word instead of 2 for power values

### Technical Details

**16-bit Signed Handling:**
```python
def int_to_uint16(value):  # For writing
    if value < 0:
        return value + 65536  # Two's complement
    return value & 0xFFFF

def uint16_to_int(value):  # For reading
    if value >= 32768:  # Negative (high bit set)
        return value - 65536
    return value
```

**Value Range:**
- 16-bit signed: -32768 to +32767
- In hW (hectowatts): ±3276.7 kW
- Sufficient for configured limits (±1000 kW / ±600 kvar)

### Register Map (After Change)

| Register | Local | Remote | Size | Type |
|----------|-------|--------|------|------|
| p_setpoint_in | 0 | 86 | 1 word | signed |
| p_battery | 2 | 270 | 1 word | signed |
| q_setpoint_in | 4 | 88 | 1 word | signed |
| q_battery | 6 | 272 | 1 word | signed |
| enable | 10 | 1 | 1 word | unsigned |
| soc | 12 | 281 | 1 word | unsigned |
| p_poi | 14 | 290 | 1 word | signed |
| q_poi | 16 | 292 | 1 word | signed |
| v_poi | 18 | 296 | 1 word | unsigned |

---

## Previous Focus
Implemented log file selector dropdown to replace the "Clear Display" button in the Logs tab. Users can now browse historical log files from the logs/ folder instead of only viewing current session logs.

## Recent Changes (2026-02-02) - Dashboard State Transition Improvements

### Overview
Fixed state transition delay and added intermediate states for better user feedback:
1. **Dashboard refresh interval** now uses `measurement_period_s` from config (1s instead of 2s)
2. **Status check** runs every interval instead of every 6 seconds
3. **Intermediate states**: "Starting..." and "Stopping..." with animated badges
4. **Instant feedback**: Status updates immediately when buttons are clicked

### Files Modified
- **[`dashboard_agent.py`](dashboard_agent.py)**:
  - Dashboard interval uses `config.get('MEASUREMENT_PERIOD_S', 1)` instead of hardcoded 2s
  - Status check runs on every interval (removed `if n % 3 == 0` condition)
  - Added intermediate "Starting..." and "Stopping..." states
  - Both buttons disabled during transitions
  - Added `system-status` store as callback input for instant updates
  
- **[`assets/custom.css`](assets/custom.css)**:
  - Added `.status-badge-starting` (blue with pulse animation)
  - Added `.status-badge-stopping` (purple with pulse animation)
  - Added `@keyframes pulse` animation

### State Transition Flow
```
Stopped → [Click Start] → Starting... (blue, pulse, 1-2s) → Running (green)
Running → [Click Stop]  → Stopping... (purple, pulse, 1-2s) → Stopped (red)
```

### Implementation Details

**Intermediate State Logic:**
```python
if system_status == 'starting' and actual_status != 1:
    # Show "Starting..." until Modbus confirms running
    status_text = "Starting..."
    status_class = "status-badge status-badge-starting"
elif system_status == 'stopping' and actual_status != 0:
    # Show "Stopping..." until Modbus confirms stopped
    status_text = "Stopping..."
    status_class = "status-badge status-badge-stopping"
elif actual_status == 1:
    status_text = "Running"  # Final state
elif actual_status == 0:
    status_text = "Stopped"  # Final state
```

**CSS Animations:**
```css
.status-badge-starting {
    background: #dbeafe;
    color: #2563eb;
    animation: pulse 1.5s ease-in-out infinite;
}

@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.7; }
}
```

---

## Recent Changes (2026-02-02) - Enhanced Logging System

### Overview
Implemented a comprehensive logging system with three outputs:
1. **Console**: Existing behavior maintained
2. **File**: Daily rotating log files in `logs/` folder
3. **Dashboard**: Real-time log display in new Logs tab

### Features
**Log File Management:**
- Daily rotating files: `logs/hil_scheduler_YYYY-MM-DD.log`
- 30-day retention (configurable backupCount)
- Auto-creation of logs/ directory
- Added to .gitignore (not committed)

**Dashboard Logs Tab:**
- New 4th tab in dashboard: "Logs"
- Real-time display of session logs (auto-refresh every 2s)
- Color-coded by level (ERROR=red, WARNING=orange, INFO=green, DEBUG=gray)
- Shows log file path
- Clear Display button (clears dashboard view, not file)
- Maximum 1000 entries in session buffer (prevents memory bloat)

### Files Modified
- **[`logger_config.py`](logger_config.py)** (NEW): Logger configuration module
  - `SessionLogHandler`: Custom handler capturing to shared_data
  - `setup_logging()`: Configures console, file, and session handlers
- **[`hil_scheduler.py`](hil_scheduler.py)**: 
  - Replaced `logging.basicConfig()` with `setup_logging()`
  - Added `session_logs` and `log_lock` to shared_data
  - Added `log_file_path` to shared_data
- **[`dashboard_agent.py`](dashboard_agent.py)**:
  - Added "Logs" tab button and content area
  - Updated tab switching callback for 4 tabs
  - Added `update_logs_display()` callback with color coding
- **[`.gitignore`](.gitignore)**: Added `logs/` directory

### Implementation Details

**Logger Architecture:**
```python
# Three handlers configured in setup_logging()
1. Console Handler       → stdout
2. TimedRotatingFileHandler → logs/hil_scheduler.log (rotates daily)
3. SessionLogHandler     → shared_data['session_logs'] (for dashboard)
```

**Session Log Entry Format:**
```python
{
    'timestamp': '14:32:05',
    'level': 'INFO',
    'message': 'Director agent starting the application.'
}
```

**Shared Data Structure (Updated):**
```python
shared_data = {
    # Existing fields...
    "session_logs": [],          # List of log entries for dashboard
    "log_lock": threading.Lock(),  # Protects session_logs
    "log_file_path": None,       # Path to current log file
}
```

### Previous Focus
Schedule type switching confirmation modal implemented. Both plant and schedule switches now flush measurements to CSV and clear the measurements DataFrame before switching.

## Recent Changes (2026-02-02) - Schedule Switch Confirmation

### Overview
Implemented confirmation modal for schedule type switching (Manual ↔ API) and ensured both plant and schedule switches properly flush and clear measurements.

### Behavior
**When switching schedule type:**
1. Confirmation modal appears with message: "Switching schedule source will stop the system and flush current measurements. Continue?"
2. On Cancel: Modal closes, no changes made
3. On Confirm:
   - System is stopped via Modbus
   - Measurements flushed to current CSV file (if any)
   - Measurements DataFrame cleared
   - `measurements_filename` cleared
   - `active_schedule_source` updated in shared_data
   - Modal closes, UI updates

**When switching plants (updated):**
- Same behavior: stops system, flushes measurements, clears DataFrame, then switches

### Files Modified
- [`dashboard_agent.py`](dashboard_agent.py):
  - Added schedule switch confirmation modal UI
  - Added `stop_system()` helper function
  - Added `flush_and_clear_measurements()` helper function
  - Consolidated schedule selection into single callback with confirmation flow
  - Updated plant switch to also flush and clear measurements
- [`hil_scheduler.py`](hil_scheduler.py):
  - Added `schedule_switching` flag to shared_data

### Implementation Details

**Helper Functions:**
- `stop_system()`: Stops the system by writing 0 to enable register
- `flush_and_clear_measurements()`: Flushes measurements to CSV and clears DataFrame

**Callback Consolidation:**
- Merged two conflicting callbacks into single `select_active_source` callback
- Handles: initial load, schedule button clicks (show modal), cancel, confirm
- Pattern matches the plant selection callback implementation

### Previous Focus
Compact Status & Plots tab UI implemented. Unified control card with simple toggle switches for Schedule and Plant selection, integrated Start/Stop buttons, and responsive layout for small screens.

## Recent Changes (2026-02-01) - Two-Row Control Panel

### Overview
Redesigned the Status & Plots tab with a compact two-row control panel:

**Before:**
- Separate cards for Plant Selection, Active Schedule, Control Buttons
- Large mode-option labels taking significant space
- Status bar separate from controls
- Multiple visual elements scattered across the tab

**After:**
- Two-row control panel with all controls organized
- **Row 1**: Start/Stop buttons, Schedule toggle, Plant toggle (1/3 width each on medium+)
- **Row 2**: Status badge and status messages (flowing with wrap on small screens)
- Responsive: full-width stacking on small screens

### Visual Structure (Medium+ Screens):
```
┌───────────────────────────────────────────────────────────────────┐
│  [▶ Start ■ Stop]  [Schedule: Manual API]  [Plant: Local Remote]  │
├───────────────────────────────────────────────────────────────────┤
│     ● Running | Source: Manual | API: Not connected | 21:30       │
└───────────────────────────────────────────────────────────────────┘
```

### Visual Structure (Small Screens):
```
┌──────────────────────────────────────┐
│        [▶ Start] [■ Stop]            │
│  Schedule: [    Manual    API    ]   │
│  Plant:    [    Local    Remote  ]   │
├──────────────────────────────────────┤
│  ● Running | Source: Manual | API:   │
│  Not connected | 21:30               │
└──────────────────────────────────────┘
```

### Layout Structure:
- **Row 1 - Controls (1/3 width each on medium+)**:
  1. **Control Section (1/3)**: Start/Stop buttons (full-width on small screens)
  2. **Control Section (1/3)**: Schedule label + Manual/API toggle
  3. **Control Section (1/3)**: Plant label + Local/Remote toggle

- **Row 2 - Status (flowing with wrap)**:
  - Status badge (Running/Stopped/Unknown)
  - Source: Manual/API
  - API connection status with point counts
  - Last update timestamp

### Responsive Behavior:
- **Desktop (>768px)**: Controls in 3 columns, status in single row
- **Tablet (≤768px)**: Same layout, smaller padding/fonts
- **Mobile (≤640px)**: Controls full-width stacked, status flows with wrap

### CSS Classes:
- `.control-panel` - Main container with two rows
- `.controls-row` - Row 1: buttons + toggles (3-column layout)
- `.control-section` - Each 1/3 section
- `.control-group` - Start/Stop button container
- `.control-btn` - Compact buttons (flex: 1 on small screens)
- `.toggle-wrapper` - Label + toggle container
- `.compact-toggle` - Horizontal toggle switch (flex: 1 on small screens)
- `.toggle-option` - Individual toggle button
- `.status-row` - Row 2: status messages (wrap on small screens)
- `.status-badge` - Compact status indicator (pill style)
- `.status-text` - Status message text

### Files Modified:
- [`dashboard_agent.py`](dashboard_agent.py): Two-row control panel layout
- [`assets/custom.css`](assets/custom.css): Control panel styles with responsive breakpoints

### Responsive Breakpoints:
- Desktop (>768px): 3-column controls, status in row
- Tablet (≤768px): Same layout, smaller elements
- Mobile (≤640px): Full-width stacked controls, flowing status messages

### Key Features:
- Full-width buttons and toggles on small screens
- Status messages flow horizontally with wrapping
- Clean visual hierarchy with labeled sections
- Plant switch confirmation modal preserved
- Status messages restored (Source, API, Last update)

---

## Previous: Dual Plant Support

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
- Run manual end-to-end validation of decoupled controls on both local and remote plants:
  - Start/Stop only affects scheduler/plant operation
  - Record/Stop only affects file recording
- Validate safe-stop timeout path with unavailable/slow plant responses
- Add automated tests for `scheduler_running` gating and recording rotation behavior
- Confirm schedule switch preserves recording session and measurement DataFrame
- Confirm plant switch flushes/clears measurements and prevents mixed datasets

## Architecture Notes

### New Data Flow (Measurement Files)
```
User clicks Record:
  Dashboard → generates filename "data/20260201_154500_data.csv"
  Dashboard → stores in shared_data['measurements_filename']
  
Measurement Agent (polling every 1s):
  Detects filename change → flushes old data → clears DataFrame → starts new file
  
User clicks Record Stop:
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
    "scheduler_running": False,
    
    # API configuration
    "api_password": None,
    "data_fetcher_status": {...},
    
    # Measurement file (recording control)
    "measurements_filename": None,  # Set by dashboard, polled by measurement agent
    
    # Existing data
    "measurements_df": pd.DataFrame(),
    "lock": threading.Lock(),
    "shutdown_event": threading.Event(),
}
```

### Key Files
- [`dashboard_agent.py`](dashboard_agent.py): Scheduler control + safe stop + recording controls
- [`scheduler_agent.py`](scheduler_agent.py): Dispatch gating via `scheduler_running`
- [`measurement_agent.py`](measurement_agent.py): Filename polling + measurement logging
- [`hil_scheduler.py`](hil_scheduler.py): Updated shared data with `measurements_filename` + `scheduler_running`
- [`data/`](data/): New folder for measurement files
