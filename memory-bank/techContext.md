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

### Multi-threaded Agent Pattern
Each agent runs in its own thread with:
- Independent execution loop with configurable period
- Shared data structures protected by threading.Lock
- Shutdown event for graceful termination
- Exception handling to prevent thread crashes

### Modbus Communication Pattern
```
Python Float (kW/kWh) 
    ↓ [convert_units]
Modbus Value (hW/hWh or encoded)
    ↓ [write_to_register]
16-bit Unsigned Register
```

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
    
    # Plant selection (NEW - dual plant support)
    "selected_plant": "local",              # 'local' or 'remote'
    "plant_switching": False,               # True during plant switch
    
    # API configuration
    "api_password": None,                   # Set by dashboard
    "data_fetcher_status": {                # Set by data fetcher
        "connected": False,
        "today_fetched": False,
        "tomorrow_fetched": False,
        "today_points": 0,
        "tomorrow_points": 0,
        "last_attempt": None,
        "error": None,
    },
    
    # Measurement file management
    "measurements_filename": None,          # Set by dashboard, polled by agent
    "measurements_df": pd.DataFrame(),      # Logged measurements
    
    # Threading
    "lock": threading.Lock(),               # Thread synchronization
    "shutdown_event": threading.Event(),    # Graceful shutdown signal
}
```

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
- Other agents use their respective `_PERIOD_S` config values consistently

### Agent Timing Strategy

**Data Fetcher Agent:**
- Uses single polling interval from `DATA_FETCHER_PERIOD_S` config (default: 120s)
- Uses hardcoded 30s backoff for all error conditions (no password, auth error, fetch error, unexpected error)
- Logs timing configuration at startup for transparency

**Other Agents (Consistent Pattern):**
- Scheduler: Uses `SCHEDULER_PERIOD_S` config
- Plant: Uses `PLANT_PERIOD_S` config  
- Measurement: Uses `MEASUREMENT_PERIOD_S` and `MEASUREMENTS_WRITE_PERIOD_S` config

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

### Plant Agent (Unified Server)
| Register | Address | Type | Description |
|----------|---------|------|-------------|
| SETPOINT_IN | 0 (local) | 32-bit signed | Power setpoint from scheduler (hW) |
| SETPOINT_ACTUAL | 2 (local) | 32-bit signed | Actual power after limiting (hW) |
| ENABLE | 10 (local) | 16-bit unsigned | 0=disabled, 1=enabled |
| SOC | 12 (local) | 16-bit unsigned | State of Charge (×10000) |
| P_POI | 14 (local) | 32-bit signed | Active power at POI (hW) |
| Q_POI | 16 (local) | 32-bit signed | Reactive power at POI (hW) |
| V_POI | 18 (local) | 16-bit unsigned | Voltage at POI (×100) |

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
2026-01-28 18:03:30.496497,602.4,602.4,0.8125,602.3,0.0,0.9998
...
```
- Written periodically (default 2 seconds)
- Contains both desired and actual power
- Contains POI measurements (P, Q, V)
- SoC and voltage in per-unit (0.0 to 1.0+)

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
