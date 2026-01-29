# Technical Context: HIL Scheduler

## Technology Stack

### Core Language
- **Python 3.x**: Primary development language

### Key Dependencies (from requirements.txt)
| Package | Purpose |
|---------|---------|
| `dash` | Web dashboard framework |
| `numpy` | Numerical computations and random generation |
| `pandas` | DataFrame operations for schedules and measurements |
| `plotly` | Interactive plotting for dashboard |
| `pyModbusTCP` | Modbus TCP client and server implementation |

### Development Environment
- Virtual environment recommended (venv)
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
| Energy/SoC | float kWh | unsigned int hWh | ×10 or ÷10 |
| SoC (per-unit) | float (0-1) | unsigned int ×10000 | ×10000 or ÷10000 |

**Key Conversion Functions** (in [`utils.py`](utils.py)):
- [`kw_to_hw()`](utils.py:5): Convert kW to hW (hectowatts)
- [`hw_to_kw()`](utils.py:8): Convert hW to kW

### Shared Data Pattern
```python
shared_data = {
    "schedule_final_df": pd.DataFrame(),  # 1-second resolution schedule
    "measurements_df": pd.DataFrame(),    # Logged measurements
    "lock": threading.Lock(),             # Thread synchronization
    "shutdown_event": threading.Event(),  # Graceful shutdown signal
}
```

## Configuration System

### Configuration Modes
The [`config.py`](config.py) provides two modes via `configure_scheduler()`:

1. **Remote Mode** (`remote_plant=True`): Connects to real hardware
   - PPC at 10.117.133.21:502
   - Battery at 10.117.133.21:502
   - Battery capacity: 500 kWh

2. **Local Mode** (`remote_plant=False`): Local emulation
   - PPC at localhost:5020
   - Battery at localhost:5021
   - Battery capacity: 50 kWh

### Key Configuration Parameters
```python
{
    # Schedule Generation
    "SCHEDULE_DURATION_H": 0.5,           # Hours of schedule
    "SCHEDULE_POWER_MIN_KW": -1000,       # Min power
    "SCHEDULE_POWER_MAX_KW": 1000,        # Max power
    
    # Agent Periods
    "DATA_FETCHER_PERIOD_S": 1,           # Schedule refresh
    "SCHEDULER_PERIOD_S": 1,              # Setpoint check
    "PPC_PERIOD_S": 5,                    # PPC cycle
    "BATTERY_PERIOD_S": 5,                # Battery simulation
    "MEASUREMENT_PERIOD_S": 2,            # Data logging
}
```

## Modbus Register Map

### PPC Agent (Server)
| Register | Address | Type | Description |
|----------|---------|------|-------------|
| SETPOINT | 0 (local) / 86 (remote) | 32-bit signed | Power setpoint in hW |
| ENABLE | 10 (local) / 1 (remote) | 16-bit unsigned | 0=disabled, 1=enabled |

### Battery Agent (Server)
| Register | Address | Type | Description |
|----------|---------|------|-------------|
| SETPOINT_IN | 0 | 32-bit signed | Incoming power setpoint (hW) |
| SETPOINT_ACTUAL | 2 (local) / 86 (remote) | 32-bit signed | Applied power after limiting (hW) |
| SOC | 10 (local) / 281 (remote) | 16-bit unsigned | State of Charge (×10000) |

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
timestamp,original_setpoint_kw,actual_setpoint_kw,soc_pu
2026-01-28 18:03:30.496497,602.4,602.4,0.8125
...
```
- Written periodically (default 2 seconds)
- Contains both desired and actual (limited) power
- SoC in per-unit (0.0 to 1.0+)

## Threading Model

```
Main Thread (Director)
├── Data Fetcher Thread
├── Scheduler Thread
├── Measurement Thread
├── Dashboard Thread (spawns Dash server thread)
├── PPC Thread (if local)
└── Battery Thread (if local)
```

Each thread:
1. Initializes connections/servers
2. Runs main loop until shutdown_event
3. Sleeps for configured period
4. Handles cleanup on exit
