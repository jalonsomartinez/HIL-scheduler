# Active Context: HIL Scheduler

## Current Focus
Application tested and working in local mode. System is fully operational.

## Recent Changes
- Analyzed all source code files to understand system architecture
- Created comprehensive Memory Bank structure with 7 documentation files
- Documented agent patterns, data flows, and configuration system
- Set up Python virtual environment (venv/)
- Installed all dependencies from requirements.txt (dash, numpy, pandas, plotly, pyModbusTCP)
- Removed legacy documentation (instructions.md, specs.md, get-pip.py)
- Created detailed project plan with phased improvements
- Changed hil_scheduler.py to run in LOCAL mode (REMOTE_PLANT = False)
- Successfully tested application - all agents working correctly

## Next Steps
Based on the project plan, recommended implementation order:
1. Add .gitignore file
2. Improve README.md with quick start guide
3. Configuration Management - External config files + CLI args
4. Code Quality Tools - black, flake8, mypy, pre-commit
5. Unit Tests - pytest coverage for core functionality
6. Error Handling - Retry logic and resilience improvements
7. Enhanced Dashboard - Additional UI features

## Quick Wins Available
- Add .gitignore file
- Improve README.md with quick start guide
- Add requirements-dev.txt for development dependencies
- Config validation in config.py

## Active Decisions

### Memory Bank Strategy
- Converting all project knowledge from ad-hoc markdown files to structured Memory Bank format
- Will delete `instructions.md` and `specs.md` once Memory Bank is complete
- README.md can remain as high-level project overview

### Environment Setup
- Using standard Python venv for isolation
- All dependencies already specified in requirements.txt
- Project is cross-platform compatible (Windows/Linux/macOS)

## Important Patterns

### Code Organization
- Each agent is in its own file with clear naming convention: `{agent_name}_agent.py`
- Shared utilities in `utils.py`
- Configuration centralized in `config.py`
- Main entry point: `hil_scheduler.py` (Director agent)

### Modbus Addressing
Different register addresses for local vs remote modes:
- Local mode uses simple sequential addresses (0, 2, 10)
- Remote mode uses actual hardware addresses (86, 1, 281)
- This is controlled entirely by config.py

### Data Resolution Chain
1. **5-minute**: Initial random schedule generation
2. **1-minute**: Interpolated and stored in CSV
3. **1-second**: Forward-filled for execution
4. **As executed**: Logged to measurements.csv

## Known Considerations

### Thread Safety
All agents share `shared_data` dict with:
- `schedule_final_df`: Read by Scheduler, written by Data Fetcher
- `measurements_df`: Written by Measurement, read by Dashboard
- `lock`: threading.Lock() for DataFrame access
- `shutdown_event`: threading.Event() for graceful shutdown

### Modbus Conversions
Critical to handle unit conversions at Modbus boundaries:
- Power: kW (Python) ↔ hW (Modbus, signed)
- SoC: kWh (Python) ↔ hWh (Modbus, unsigned)
- Use `get_2comp` and `word_list_to_long` for 32-bit values
