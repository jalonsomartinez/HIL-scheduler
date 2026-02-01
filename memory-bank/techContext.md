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
    
    # Existing data
    "measurements_df": pd.DataFrame(),      # Logged measurements
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

### YAML Configuration (Simulated Plant)
The [`config.yaml`](config.yaml) file provides configuration for the simulated plant:

```yaml
general:
  log_level: INFO
  schedule_duration_h: 0.5

timing:
  plant_period_s: 5
  measurement_period_s: 2

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
