# Active Context: HIL Scheduler

## Current Focus
Application has been refactored with merged Plant Agent and plant model simulation. PPC and Battery agents have been merged into a single Plant Agent.

## Recent Changes (2026-01-30)

### Major Refactoring
1. **Agent Merge**: Merged `ppc_agent.py` and `battery_agent.py` into `plant_agent.py`
   - Single Modbus server interface (PPC interface)
   - Internal battery simulation (no separate Modbus server)
   - Simplified architecture with fewer moving parts

2. **Plant Model**: Added impedance model between battery and POI
   - Configurable impedance parameters (R=0.01Ω, X=0.1Ω)
   - Calculates P_poi, Q_poi, and V_poi
   - Unity power factor (configurable)

3. **YAML Configuration**: Moved simulated plant config to YAML
   - Created `config.yaml` with plant model parameters
   - Created `config_loader.py` to parse YAML
   - Retained `config.py` for HIL plant (remote mode)

4. **New Measurements**: Added POI values to data logging
   - `p_poi_kw` - Active power at POI
   - `q_poi_kvar` - Reactive power at POI
   - `v_poi_pu` - Voltage at POI

5. **Dashboard Updates**: Extended to show POI measurements
   - Added P_poi trace to power graph
   - Added Q_poi subplot
   - Shows voltage at POI

6. **Code Cleanup**: Deleted deprecated files
   - Deleted `ppc_agent.py`
   - Deleted `battery_agent.py`
   - Retained `config.py` for HIL plant configuration

## Next Steps
1. Functional testing of the refactored system
2. Consider unified configuration approach for both plant modes
3. Update README.md with new architecture documentation

## Architecture Changes

### Before
- PPC Agent → Modbus → Battery Agent → Modbus
- Two separate servers (ports 5020 and 5021)
- Two clients in measurement agent

### After
- Scheduler → Modbus → Plant Agent (single server)
- Internal battery simulation
- Single client in measurement agent
- POI calculations computed inside Plant Agent

## Configuration Files

### config.yaml (Simulated Plant)
Used by `hil_scheduler.py` for local simulation mode.
Contains plant model parameters and Modbus register map.

### config.py (HIL Plant)
Retained for future remote/HIL mode implementation.
Contains real hardware configuration.

## New Register Map (Plant Agent)

| Address | Size | Name | Description |
|---------|------|------|-------------|
| 0-1 | 2 words | SETPOINT_IN | Power setpoint from scheduler (hW, signed 32-bit) |
| 2-3 | 2 words | SETPOINT_ACTUAL | Actual power after SoC limiting (hW, signed 32-bit) |
| 10 | 1 word | ENABLE | Enable flag (0=disabled, 1=enabled) |
| 12 | 1 word | SOC | State of Charge (per-unit x10000) |
| 14-15 | 2 words | P_POI | Active power at POI (hW, signed 32-bit) |
| 16-17 | 2 words | Q_POI | Reactive power at POI (hW, signed 32-bit) |
| 18 | 1 word | V_POI | Voltage at POI (per-unit x100) |

## Important Patterns

### Code Organization
- Each agent is in its own file with clear naming convention: `{agent_name}_agent.py`
- Shared utilities in `utils.py`
- Configuration: `config.yaml` for simulation, `config_loader.py` for parsing
- Main entry point: `hil_scheduler.py` (Director agent)

### Thread Safety
All agents share `shared_data` dict with:
- `schedule_final_df`: Read by Scheduler, written by Data Fetcher
- `measurements_df`: Written by Measurement, read by Dashboard
- `lock`: threading.Lock() for DataFrame access
- `shutdown_event`: threading.Event() for graceful shutdown

### Modbus Conversions
Critical to handle unit conversions at Modbus boundaries:
- Power: kW (Python) ↔ hW (Modbus, signed)
- SoC: pu (Python) ×10000 ↔ register (unsigned)
- Voltage: pu (Python) ×100 ↔ register (unsigned)
- Use `get_2comp` and `word_list_to_long` for 32-bit values
