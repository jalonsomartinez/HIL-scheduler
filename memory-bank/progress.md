# Progress: HIL Scheduler

## What Works

### Core Agents
- [x] **Director Agent** (`hil_scheduler.py`): Main orchestrator, starts/stops all threads, handles shutdown
- [x] **Data Fetcher Agent** (`data_fetcher_agent.py`): Generates random schedules, interpolates 5min→1min, creates 1-second resolution DataFrame
- [x] **Scheduler Agent** (`scheduler_agent.py`): Reads schedule, sends setpoints to Plant via Modbus
- [x] **Plant Agent** (`plant_agent.py`): Merged PPC + Battery functionality with internal simulation and POI calculations
- [x] **Measurement Agent** (`measurement_agent.py`): Logs data from Plant agent to CSV (including POI measurements)
- [x] **Dashboard Agent** (`dashboard_agent.py`): Dash web UI with real-time graphs and controls

### Communication Layer
- [x] Modbus TCP client/server implementation using pyModbusTCP
- [x] 32-bit signed integer encoding/decoding for power values
- [x] Unit conversions (kW↔hW, kWh↔hWh) in utils.py

### Configuration
- [x] YAML configuration file (`config.yaml`) for the simulated plant
- [x] Config loader module (`config_loader.py`) to parse YAML into flat dictionary
- [x] Configuration includes plant model parameters (R, X, V_nominal, power factor)
- [x] Legacy config.py retained for HIL plant configuration (remote mode)

### Data Files
- [x] `schedule_source.csv`: Generated schedule (1-min resolution)
- [x] `measurements.csv`: Logged measurements with timestamps and POI values

### Threading
- [x] Proper threading.Lock for shared DataFrame access
- [x] threading.Event for graceful shutdown signaling
- [x] Thread.join() for clean termination

### Plant Model
- [x] Impedance model between battery and POI (R + jX)
- [x] Calculates P_poi and Q_poi based on impedance and power flow
- [x] Computes V_poi (voltage at point of interconnection)
- [x] Exposes POI measurements via Modbus registers

## What's Left to Build

### Potential Improvements (Not Yet Planned)
- [ ] Command-line argument parsing for runtime configuration
- [ ] More sophisticated schedule generation (not just random)
- [ ] Schedule validation and preview before execution
- [ ] Historical data analysis tools
- [ ] Better error recovery and retry mechanisms
- [ ] Unit tests for individual agents
- [ ] Integration tests for full workflow
- [ ] Docker containerization for easy deployment
- [ ] API endpoint for external schedule submission
- [ ] Unified configuration for both local and remote plant modes

## Current Status

### Project Phase
Application has been refactored with merged Plant Agent and plant model simulation.

### Code Quality
- Code is functional but lacks comprehensive error handling
- No unit tests present
- Logging is in place but could be more structured
- No input validation on configuration values

### Documentation Status
- [x] Memory Bank initialized with core files
- [x] Legacy docs removed (instructions.md, specs.md, get-pip.py)
- [x] Plan document created for agent merge (`plans/agent_merge_and_plant_model.md`)
- [ ] README.md could be enhanced with quick start guide

## Recent Changes (2026-01-30)

### Agent Merge
- Merged `ppc_agent.py` and `battery_agent.py` into single `plant_agent.py`
- Plant agent provides single Modbus server interface
- Battery simulation is now internal (no separate Modbus server)

### Plant Model
- Added impedance model between battery and POI
- Configurable R=0.01Ω, X=0.1Ω in YAML
- Calculates P_poi, Q_poi, and V_poi

### Configuration
- Created `config.yaml` for simulated plant configuration
- Created `config_loader.py` to load YAML configuration
- Retained `config.py` for HIL plant (remote mode) configuration

### New Measurements
- Added `p_poi_kw` to measurements.csv
- Added `q_poi_kvar` to measurements.csv
- Added `v_poi_pu` to measurements.csv

### Dashboard Updates
- Added P_poi trace to power graph
- Added Q_poi subplot
- All POI values displayed in real-time

### Deleted Files
- `ppc_agent.py` (functionality merged)
- `battery_agent.py` (functionality merged)

## Known Issues

### None
Application has been tested for syntax correctness. Functional testing pending.

## Evolution of Decisions

### Original Design (from specs.md)
- All agents specified with clear responsibilities
- Modbus communication pattern defined
- Unit conversion requirements specified

### Current Implementation
- Matches specifications closely
- Added local/remote dual mode support
- Dashboard provides more features than originally specified (better status indicator)

### Recent Refactoring (2026-01-30)
- Merged PPC and Battery agents into Plant Agent for simplified architecture
- Added plant impedance model for more realistic simulation
- Migrated simulated plant configuration to YAML format
- Single Modbus server for plant interface (no battery server needed in simulation)
