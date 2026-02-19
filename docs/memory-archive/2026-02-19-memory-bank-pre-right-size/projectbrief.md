# Project Brief: HIL Scheduler

## Overview
The HIL Scheduler is an agent-based Python application that acts as a real-time power schedule executor for grid-connected battery systems. It reads power setpoint schedules and executes them in real-time, sending all setpoint changes to a grid-connected battery.

## Core Requirements

### Primary Purpose
- Read power setpoint schedules from CSV files
- Execute schedules in real-time with second-level precision
- Interface with battery systems via Modbus TCP protocol
- Provide real-time monitoring through a web-based dashboard

### Functional Goals
1. **Schedule Management**: Generate and interpolate power schedules (5-min to 1-min resolution)
2. **Real-time Execution**: Execute power setpoints at the correct time
3. **Battery Simulation**: Track State of Charge (SoC) and apply power limits
4. **Data Logging**: Record all measurements to CSV for analysis
5. **Visualization**: Provide live dashboard with independent scheduler and recording controls

### Operating Modes
- **Local Emulation Mode**: Runs PPC and Battery as local Modbus servers (for testing)
- **Remote Hardware Mode**: Connects to real battery hardware via Modbus TCP

## Project Scope

### In Scope
- Multi-threaded agent architecture
- Modbus TCP communication layer
- Schedule generation and interpolation
- Battery SoC tracking with boundary protection
- Real-time dashboard with Plotly/Dash
- CSV-based data persistence

### Key Constraints
- Power range: -1000 kW to +1000 kW
- Default battery capacity: 50 kWh (configurable)
- Default schedule duration: 0.5 hours (configurable)
- Modbus register size: 16-bit unsigned (requires conversion for signed values)

## Success Criteria
1. Accurate schedule execution with 1-second resolution
2. Proper battery SoC tracking and limit enforcement
3. Successful Modbus communication with both local and remote hardware
4. Functional dashboard with real-time updates
5. Complete data logging for post-analysis
