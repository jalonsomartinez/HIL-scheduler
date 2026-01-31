# Progress: HIL Scheduler

## What Works

### Core Agents
- [x] **Director Agent** (`hil_scheduler.py`): Main orchestrator, starts/stops all threads, handles shutdown
- [x] **Data Fetcher Agent** (`data_fetcher_agent.py`): Uses ScheduleManager for all 3 schedule modes
- [x] **Scheduler Agent** (`scheduler_agent.py`): Reads schedule using asof(), sends setpoints to Plant via Modbus
- [x] **Plant Agent** (`plant_agent.py`): Merged PPC + Battery functionality with internal simulation and POI calculations
- [x] **Measurement Agent** (`measurement_agent.py`): Logs data from Plant agent to CSV (including POI measurements)
- [x] **Dashboard Agent** (`dashboard_agent.py`): Dash web UI with real-time graphs, controls, and mode selection

### Schedule Management
- [x] **ScheduleManager** (`schedule_manager.py`): Unified interface for all 3 schedule modes
- [x] **Mode 1: Random Schedule**: Generates random schedules at configurable resolution (default 5-min)
- [x] **Mode 2: CSV Upload**: Upload CSV files with custom start time via dashboard
- [x] **Mode 3: Istentore API**: Fetch day-ahead schedules with automatic polling for next day
- [x] **Flexible Resolution**: Schedule DataFrame preserves original time resolution (5-min from API, any from CSV)
- [x] **Smart Data Replacement**: New data replaces only overlapping periods, preserving non-overlapping data
- [x] **asof() Lookup**: Scheduler uses pandas asof() to find value just before current time

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
- [x] `schedule_source.csv`: Generated schedule (flexible resolution)
- [x] `measurements.csv`: Logged measurements with timestamps and POI values
- [x] `istentore_api.py`: Istentore API wrapper with session-based password handling

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

## Recent Changes (2026-01-31)

### Extended Setpoint Modes Implementation
- Added 3 schedule modes selectable from dashboard:
  1. **Random Mode**: Generate random schedules (5-min resolution by default)
  2. **CSV Mode**: Upload CSV files with selectable start time
  3. **API Mode**: Fetch schedules from Istentore API with polling
- Created `istentore_api.py` wrapper class:
  - Session-based password handling
  - Day-ahead schedule fetching
  - Automatic token refresh
- Created `schedule_manager.py` central module:
  - Handles all 3 schedule modes
  - Supports flexible time resolution
  - Smart data replacement (only overlapping periods)
  - Automatic polling for next-day API schedules
- Modified `data_fetcher_agent.py`:
  - Uses ScheduleManager instead of direct CSV generation
  - Handles mode changes from dashboard
- Modified `dashboard_agent.py`:
  - Added mode selection radio buttons
  - Added controls for each mode
  - Added schedule preview graph
- Modified `config.yaml`:
  - Added Istentore API settings
  - Added schedule default settings
- Modified `config_loader.py`:
  - Added loading of new configuration sections
- Modified `hil_scheduler.py`:
  - Runs indefinitely (no fixed end time)
  - Uses new shared data structure with schedule_manager
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

### Setpoint Naming Cleanup (2026-01-30)
- Renamed Modbus registers for clarity:
  - `setpoint_in` → `p_setpoint_in` (active power setpoint)
  - `setpoint_actual` → `p_setpoint_actual` (actual battery active power)
- Added reactive power registers:
  - `q_setpoint_in` (reactive power setpoint)
  - `q_setpoint_actual` (actual battery reactive power)
- Removed redundant `original_setpoint_kw` from measurements
- Renamed `actual_setpoint_kw` → `battery_active_power_kw`
- Added power limits configuration (p_max_kw, p_min_kw, q_max_kvar, q_min_kvar)

### Reactive Power Support (2026-01-30)
- Added reactive power schedule generation (independent of active power)
- Scheduler now sends both P and Q setpoints to plant
- Reactive power is limited by plant limits (NOT by SoC)
- Battery follows Q setpoint always within its limits
- Added Q setpoint and Q battery actual to measurements
- Dashboard now shows Q setpoint, Q battery actual, and Q at POI

### New Measurements
- Added `p_poi_kw` to measurements.csv
- Added `q_poi_kvar` to measurements.csv
- Added `v_poi_pu` to measurements.csv
- Added `p_setpoint_kw` to measurements.csv
- Added `battery_active_power_kw` to measurements.csv
- Added `q_setpoint_kvar` to measurements.csv
- Added `battery_reactive_power_kvar` to measurements.csv

### Dashboard Updates
- Added P_poi trace to power graph
- Added Q_poi subplot
- All POI values displayed in real-time
- Added Q setpoint and Q battery actual traces

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
