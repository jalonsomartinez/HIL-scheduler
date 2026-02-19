# Technical Context: HIL Scheduler

## Technology Stack

### Core Language
- **Python 3.x**: Primary development language

### Key Dependencies (from requirements.txt)
| Package | Purpose |
|---------|---------|
| `dash` | Web dashboard framework |
| `numpy` | Numerical computations and complex numbers for plant model |
| `pandas` | DataFrame operations for schedules and measurements |
| `plotly` | Interactive plotting for dashboard |
| `pyModbusTCP` | Modbus TCP client and server implementation |
| `PyYAML` | YAML configuration file parsing |

### Development Environment
- Virtual environment (venv) for dependency management
- Cross-platform (Windows, Linux, macOS)

## Architecture Patterns

### Dual-Plant Runtime Contract (2026-02-19)

- Config loader now exposes canonical plant-centric runtime keys:
  - `PLANTS`: nested dict keyed by `lib` and `vrfb`
  - `PLANT_IDS`: `('lib', 'vrfb')`
  - `STARTUP_TRANSPORT_MODE`: global startup transport (`local`/`remote`)
- `config.yaml` canonical schema is now:
  - `startup.transport_mode`
  - `plants.lib` and `plants.vrfb` with:
    - `model`
    - `modbus.local` and `modbus.remote`
    - `measurement_series`
- Legacy compatibility remains for one transition cycle:
  - old top-level `modbus_local`/`modbus_remote` and `measurement_series_by_plant` are translated into `PLANTS` with warnings.
- Shared runtime state is per-plant for schedule/recording/plot caches:
  - `manual_schedule_df_by_plant`
  - `api_schedule_df_by_plant`
  - `scheduler_running_by_plant`
  - `plant_transition_by_plant`
  - `measurements_filename_by_plant`
  - `current_file_path_by_plant`
  - `current_file_df_by_plant`

### Dashboard Runtime UX Contract (2026-02-19)

- `dashboard_agent.py` now uses four tabs (`Status & Plots`, `Manual Schedule`, `API Schedule`, `Logs`).
- Logs UI contract:
  - `log-file-selector` options refreshed from `logs/*.log`,
  - `logs-display` shows either live session logs or parsed historical file content.
- Schedule source UI contract:
  - source toggle is modal-confirmed via `schedule-switch-modal`,
  - confirm path safe-stops both plants before backend source update.
- Plot persistence contract:
  - Plotly `layout.uirevision` is set for both plant charts and preview graphs to preserve zoom/pan across interval refreshes.

### API Posting Observability Runtime Contract (2026-02-19)

- New shared runtime key initialized in `hil_scheduler.py`:
  - `measurement_post_status` (dict keyed by `lib` / `vrfb`).
- `measurement_agent.py` now populates posting telemetry in real time:
  - posting mode enabled flag,
  - last enqueue timestamp,
  - last attempt payload/result/error/retry ETA,
  - last successful post payload,
  - last error summary,
  - per-plant queue depth and oldest pending age.
- API payload queue entries are enriched with attribution context:
  - `plant_id` and `metric` (`soc|p|q|v`) are carried with each queue item.
- `dashboard_agent.py` API tab now renders posting telemetry:
  - callback adds output `api-measurement-posting-status`,
  - UI section renders per-plant cards under API connection status with success/attempt/error/queue details.
- Timestamp display policy in API tab:
  - values are normalized and displayed in configured timezone via existing `time_utils` helpers.

### Timezone Utilities and Conventions (2026-02-17)

- New module: [`time_utils.py`](time_utils.py)
  - `get_config_tz(config)` returns `ZoneInfo` from `TIMEZONE_NAME`.
  - `now_tz(config)` returns aware current datetime in configured timezone.
  - `normalize_timestamp_value()` and `normalize_datetime_series()` normalize naive/aware timestamps to configured timezone.
  - `normalize_schedule_index()` normalizes schedule dataframe indexes.
  - `serialize_iso_with_tz()` serializes timestamps as ISO 8601 with timezone offset.
- Config source of truth: `config.yaml` key `time.timezone` (default `Europe/Madrid`), flattened as `TIMEZONE_NAME` by `config_loader.py`.
- Runtime policy: internal schedule + measurement timestamps are timezone-aware and normalized to configured timezone.
- Backward compatibility: naive legacy timestamps are interpreted as configured timezone.

### Measurement API Payload Hardening (2026-02-19)

- `measurement_agent.py` now centralizes API posting conversions in helper logic:
  - `soc_pu -> soc_kwh`
  - `p_poi_kw -> p_w`
  - `q_poi_kvar -> q_var`
  - `v_poi_pu -> v_v`
- Conversion factors are parsed/validated once at startup from config:
  - `PLANT_CAPACITY_KWH`
  - `PLANT_POI_VOLTAGE_V`
- Payload numeric validation is enforced before enqueue:
  - non-numeric, `NaN`, and `inf` values are skipped,
  - `None` values are not queued for retry,
  - warnings are logged for skipped invalid values.

### Multi-threaded Agent Pattern
Each agent runs in its own thread with:
- Independent execution loop with configurable period
- Shared data structures protected by threading.Lock
- Shutdown event for graceful termination
- Exception handling to prevent thread crashes

### Modbus Communication Pattern
All registers are 16-bit. For signed values, two's complement encoding is used.

```
Python Float (kW/kvar) 
    ↓ [kw_to_hw] - Convert to hectowatts (×10)
Signed Integer (hW)
    ↓ [int_to_uint16] - Two's complement for negatives
16-bit Unsigned Register

16-bit Unsigned Register
    ↓ [uint16_to_int] - Two's complement decode
Signed Integer (hW)
    ↓ [hw_to_kw] - Convert to kilowatts (÷10)
Python Float (kW/kvar)
```

**Key utility functions** (in [`utils.py`](../utils.py)):
- [`kw_to_hw()`](utils.py:5): Convert kW to hW (hectowatts)
- [`hw_to_kw()`](utils.py:8): Convert hW to kW
- [`int_to_uint16()`](utils.py:20): Encode signed int to 16-bit register
- [`uint16_to_int()`](utils.py:35): Decode 16-bit register to signed int

### Unit Conversions
| Type | Python Variable | Modbus Register | Conversion |
|------|----------------|-----------------|------------|
| Power | float kW | signed int hW | ×10 or ÷10 |
| Energy/SoC | float pu | unsigned int ×10000 | ×10000 or ÷10000 |
| Voltage | float pu | unsigned int ×100 | ×100 or ÷100 |

**Key Conversion Functions** (in [`utils.py`](utils.py)):
- [`kw_to_hw()`](utils.py:5): Convert kW to hW (hectowatts)
- [`hw_to_kw()`](utils.py:8): Convert hW to kW

### Shared Data Pattern (Updated)
```python
shared_data = {
    # Two separate schedules (new architecture)
    "manual_schedule_df": pd.DataFrame(),   # Manual schedule (random/CSV)
    "api_schedule_df": pd.DataFrame(),      # API-fetched schedule
    "active_schedule_source": "manual",     # 'manual' or 'api'
    "scheduler_running": False,             # Scheduler dispatch gate (True=dispatch)
    
    # Plant selection (dual plant support)
    "selected_plant": "local",              # 'local' or 'remote'
    "plant_switching": False,               # True during plant switch
    
    # API configuration
    "api_password": None,                   # Set by dashboard
    "data_fetcher_status": {                # Set by data fetcher
        "connected": False,
        "today_fetched": False,
        "tomorrow_fetched": False,
        "today_date": None,
        "tomorrow_date": None,
        "today_points": 0,
        "tomorrow_points": 0,
        "last_attempt": None,
        "error": None,
    },
    
    # Measurement file management
    "measurements_filename": None,          # Recording control signal/path (None=off)
    "measurements_df": pd.DataFrame(),      # Compatibility mirror for plotted measurement data
    "current_file_path": None,              # Selected plant/day file path used by plot cache
    "current_file_df": pd.DataFrame(),      # Selected plant/day data in memory
    "pending_rows_by_file": {},             # path -> buffered rows pending disk flush
    
    # Logging (session logs for dashboard display)
    "session_logs": [],                     # List of log entries
    "log_lock": threading.Lock(),           # Protects session_logs
    "log_file_path": None,                  # Path to current log file
    
    # Threading
    "lock": threading.Lock(),               # Thread synchronization
    "shutdown_event": threading.Event(),    # Graceful shutdown signal
}
```

**Control Decoupling Pattern (2026-02-17):**
- Scheduler control: dashboard Start/Stop toggles `scheduler_running` and plant enable/disable sequencing.
- Recording control: dashboard Record/Stop toggles `measurements_filename`; measurement agent owns null-boundary insertion + flushing.
- Schedule switch: safe plant stop only (no measurement flush).
- Plant switch: safe plant stop + measurement flush/clear.

## Logging System

### Architecture
Three-output logging system configured via [`logger_config.py`](logger_config.py):

```
┌─────────────────────────────────────────────────────────────┐
│                    Python logging                           │
└──────────────────────┬──────────────────────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        │              │              │
        ▼              ▼              ▼
   ┌─────────┐  ┌─────────────┐  ┌──────────────┐
   │ Console │  │ File (Daily)│  │ Session      │
   │ Handler │  │ Rotating    │  │ Handler      │
   │         │  │ Handler     │  │ (Dashboard)  │
   └─────────┘  └─────────────┘  └──────────────┘
        │              │              │
        ▼              ▼              ▼
     stdout      logs/ folder     shared_data
                 hil_scheduler    ['session_logs']
                 _YYYY-MM-DD.log
```

### Log File Configuration
- **Location**: `logs/hil_scheduler.log` (with date suffix)
- **Rotation**: Daily at midnight (`TimedRotatingFileHandler`)
- **Retention**: 30 days (`backupCount=30`)
- **Format**: `%(asctime)s - %(levelname)s - %(message)s`
- **Auto-create**: `logs/` directory created if missing
- **Git ignore**: Log files not committed (in `.gitignore`)

### Session Log Handler
Custom [`SessionLogHandler`](logger_config.py:16) captures logs to shared_data for dashboard display:

```python
# Log entry format in shared_data['session_logs']
{
    'timestamp': '14:32:05',
    'level': 'INFO',
    'message': 'Director agent starting the application.'
}
```

**Features:**
- Max 1000 entries (prevents memory bloat)
- Thread-safe with `log_lock`
- Dashboard displays with color coding by level

### Dashboard Logs Tab
- **Location**: 4th tab in dashboard
- **Auto-refresh**: Every 2 seconds (via interval component)
- **Display**: Scrollable dark terminal-style area
- **Color Coding**:
  - ERROR: Red (#ef4444)
  - WARNING: Orange (#f97316)
  - INFO: Green (#22c55e)
  - DEBUG: Gray (#94a3b8)
- **Controls**: Clear Display button (clears view, not files)

### Usage
```python
from config_loader import load_config
from logger_config import setup_logging

config = load_config("config.yaml")
shared_data = {"session_logs": [], "log_lock": threading.Lock()}

# Setup logging with all three handlers
setup_logging(config, shared_data)

# Use standard logging
logging.info("Application started")
logging.error("An error occurred")
```

### Thread Safety
- Session logs use separate `log_lock` (not the main `lock`)
- Lock held only for list append operation (microseconds)
- Dashboard reads with brief lock, formats outside lock
- No I/O operations inside lock (file handler writes independently)

### Manual Schedule Manager
The [`manual_schedule_manager.py`](manual_schedule_manager.py) module provides utility functions:
- [`generate_random_schedule()`](manual_schedule_manager.py:15): Create random schedule DataFrame
- [`load_csv_schedule()`](manual_schedule_manager.py:45): Load schedule from CSV file
- [`append_schedules()`](manual_schedule_manager.py:79): Merge schedules with overlap replacement
- [`get_current_setpoint()`](manual_schedule_manager.py:99): Get setpoint for current time using asof()

## Configuration System

### YAML Configuration
The [`config.yaml`](config.yaml) file provides unified configuration for both simulated and real plants:

```yaml
# Startup Configuration
startup:
  schedule_source: "manual"    # Initial schedule: "manual" or "api"
  plant: "local"               # Initial plant: "local" or "remote"

# Schedule Settings
schedule:
  source_csv: "schedule_source.csv"
  duration_h: 0.5
  default_resolution_min: 5

# Timing
timing:
  data_fetcher_period_s: 120
  scheduler_period_s: 1
  plant_period_s: 1
  measurement_period_s: 1
  measurements_write_period_s: 60

# Istentore API
istentore_api:
  poll_start_time: "17:30"
  schedule_period_minutes: 15

# Plant Model
plant:
  capacity_kwh: 50.0
  initial_soc_pu: 0.5
  power_limits:
    p_max_kw: 1000.0
    p_min_kw: -1000.0
    q_max_kvar: 600.0
    q_min_kvar: -600.0
  poi_voltage_v: 20000.0

# Modbus - Local Plant (Emulated)
modbus_local:
  host: "localhost"
  port: 5020
  registers:
    p_setpoint_in: 0
    p_battery: 2
    q_setpoint_in: 4
    q_battery: 6
    enable: 10
    soc: 12
    p_poi: 14
    q_poi: 16
    v_poi: 18

# Modbus - Remote Plant (Real Hardware)
modbus_remote:
  host: "10.117.133.21"
  port: 502
  registers:
    # Same structure as local, customize values as needed
    p_setpoint_in: 0
    # ... etc
```

**Key Configuration Notes:**
- `startup.plant`: Sets initial plant on application start ('local' or 'remote')
- `startup.schedule_source`: Sets initial schedule source ('manual' or 'api')
- Schedule generation uses `plant.power_limits` (not separate schedule limits)
- Both plants use same register structure (customize `modbus_remote` values as needed)

**Timing Configuration Notes:**
- `data_fetcher_period_s`: How often to poll Istentore API for schedule updates (default 120s = 2 minutes)
- All error conditions use hardcoded 30s backoff (not configurable)
- `poll_start_time`: Time of day (HH:MM) to start fetching tomorrow's schedule
- `schedule_period_minutes`: API setpoint validity window used by scheduler/dashboard stale cutoff (default 15 min)
- `measurement_post_period_s`: API measurement posting interval used by measurement agent (default 60s)
- `measurement_series_by_plant.*.*`: per-variable series mapping; `null` disables posting for that variable
- Other agents use their respective `_PERIOD_S` config values consistently

### Agent Timing Strategy

**Data Fetcher Agent:**
- Uses single polling interval from `DATA_FETCHER_PERIOD_S` config (default: 120s)
- Uses hardcoded 30s backoff for all error conditions (no password, auth error, fetch error, unexpected error)
- Logs timing configuration at startup for transparency

**Other Agents (Consistent Pattern):**
- Scheduler: Uses `SCHEDULER_PERIOD_S` config
- Plant: Uses `PLANT_PERIOD_S` config  
- Measurement: Uses `MEASUREMENT_PERIOD_S` as anchored step size (monotonic step scheduler), `MEASUREMENTS_WRITE_PERIOD_S` for periodic disk flushes, and `ISTENTORE_MEASUREMENT_POST_PERIOD_S` for API posting cadence

**Measurement Timing Pattern (2026-02-18):**
- Startup anchor: measurement schedule starts at startup time rounded up to next second.
- Trigger evaluation: monotonic step index with `floor((now_mono - anchor_mono) / measurement_period_s)`.
- Missed-step policy: skip missed intermediate steps; execute only latest pending step.
- Per-step retry policy: at most one measurement attempt per step even if read fails.
- Timestamp policy: persisted measurement rows use scheduled step timestamps (grid-aligned), not read-completion timestamps.

**Before (Inconsistent):**
- Data fetcher had hardcoded values (5s, 30s, 300s) that overrode config
- `ISTENTORE_POLL_INTERVAL_MIN` config was defined but never used

**After (Simplified):**
- Single config value for normal operation
- Single hardcoded value for all errors
- Predictable, documented behavior

plant:
  capacity_kwh: 50.0
  initial_soc_pu: 0.5
  impedance:
    r_ohm: 0.01
    x_ohm: 0.1
  nominal_voltage_v: 400.0
  power_factor: 1.0

modbus:
  host: "localhost"
  port: 5020
  registers:
    setpoint_in: 0
    p_poi: 14
    q_poi: 16
```

### Config Loader
The [`config_loader.py`](config_loader.py) module converts YAML to flat dictionary:

```python
from config_loader import load_config

config = load_config("config.yaml")
# Returns: {"PLANT_CAPACITY_KWH": 50.0, "PLANT_R_OHM": 0.01, ...}
```

### Legacy Configuration (HIL Plant)
The [`config.py`](config.py) file is retained for HIL (remote) plant configuration:
- Contains configuration for real hardware
- Supports dual-mode operation (remote_plant flag)
- Will be integrated with YAML system in future refactoring

## Modbus Register Map

### Plant Agent (Unified Server - All 16-bit Registers)
| Register | Local Addr | Remote Addr | Type | Description |
|----------|------------|-------------|------|-------------|
| P_SETPOINT_IN | 0 | 86 | 16-bit signed | Active power setpoint from scheduler (hW) |
| P_BATTERY | 2 | 270 | 16-bit signed | Actual active power after SoC limiting (hW) |
| Q_SETPOINT_IN | 4 | 88 | 16-bit signed | Reactive power setpoint from scheduler (hW) |
| Q_BATTERY | 6 | 272 | 16-bit signed | Actual reactive power (hW) |
| ENABLE | 10 | 1 | 16-bit unsigned | 0=disabled, 1=enabled |
| SOC | 12 | 281 | 16-bit unsigned | State of Charge (×10000) |
| P_POI | 14 | 290 | 16-bit signed | Active power at POI (hW) |
| Q_POI | 16 | 292 | 16-bit signed | Reactive power at POI (hW) |
| V_POI | 18 | 296 | 16-bit unsigned | Voltage at POI (×100) |

**16-bit Signed Range:** -32768 to +32767 hW = ±3276.7 kW (sufficient for configured ±1000 kW limits)

## Data File Formats

### schedule_source.csv
```csv
datetime,power_setpoint_kw
2026-01-28 18:03:30,602.46
...
```
- 1-minute resolution after interpolation
- Last setpoint always 0 kW

### measurements.csv
```csv
timestamp,original_setpoint_kw,actual_setpoint_kw,soc_pu,p_poi_kw,q_poi_kvar,v_poi_pu
2026-01-28T18:03:30.496497+01:00,602.4,602.4,0.8125,602.3,0.0,0.9998
...
```
- Written periodically (default 2 seconds)
- Contains both desired and actual power
- Contains POI measurements (P, Q, V)
- SoC and voltage in per-unit (0.0 to 1.0+)
- `timestamp` persisted as ISO 8601 with timezone offset (configured timezone)

## Threading Model

```
Main Thread (Director)
├── Data Fetcher Thread
├── Scheduler Thread
├── Plant Thread (merged PPC + Battery)
├── Measurement Thread
└── Dashboard Thread (spawns Dash server thread)
```

Each thread:
1. Initializes connections/servers
2. Runs main loop until shutdown_event
3. Sleeps for configured period
4. Handles cleanup on exit

### Lock Contention Best Practices
**Critical Lesson Learned (2026-02-01):**
- Holding locks during I/O or slow operations causes UI freezing
- Scheduler agent was holding lock during `asof()` lookup and Modbus writes
- Fixed by minimizing lock time to just dictionary reference operations
- Result: Dashboard is now responsive with no "Updating" delays

**Additional Optimizations (2026-02-01):**
- **Measurement Agent**: CSV write moved outside lock (~90% reduction in lock hold time)
- **Measurement Agent**: Implemented buffered measurement collection (5x less frequent locking)
- **Data Fetcher**: DataFrame operations moved outside lock (minimal impact, infrequent)

**Lock Duration Guidelines:**
| Operation | Target Lock Duration |
|-----------|---------------------|
| Dict reference read/write | < 1 ms |
| DataFrame copy (for I/O) | < 10 ms |
| DataFrame concat | Outside lock |
| CSV/network I/O | Never in lock |

**Performance Impact:**
| Lock Strategy | Dashboard Response | Status |
|---------------|-------------------|---------|
| Hold during I/O | 1-10 seconds "Updating" | ❌ Bad |
| Minimal lock time | Instant response | ✅ Good |
| Buffered writes | Instant response | ✅ Excellent |

**Thread Safety Rule:**
> Python's GIL already protects dict operations. The explicit lock is for logical synchronization, not memory safety. Get references briefly, do all work outside the lock.

## Plant Model

### Impedance Model
Simple series impedance between battery and POI:
- Z = R + jX (0.01 + j0.1 Ω default)
- Battery at one end, POI at the other
- Grid assumed to be at nominal voltage

### Calculation Steps
1. Calculate battery current from power and voltage
2. Compute voltage drop across impedance: V_drop = I × Z
3. Calculate POI voltage: V_poi = V_batt - V_drop
4. Calculate power at POI: S_poi = V_poi × I*

### Default Parameters
- R = 0.01 Ω
- X = 0.1 Ω
- V_nom = 400 V (line-to-line)
- PF = 1.0 (unity)

All parameters configurable in [`config.yaml`](config.yaml).
