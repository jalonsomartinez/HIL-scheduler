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

### Dashboard UI
- [x] Modern professional light theme with clean white surfaces
- [x] Tabbed interface (Schedule Configuration / Status & Plots)
- [x] Schedule preview with diff visualization (existing vs preview)
- [x] Preview workflow: Configure → Preview → Accept/Clear
- [x] Responsive design with CSS media queries
- [x] Color-coded status indicator (running/stopped/unknown)
- [x] Uniform styling across all components

## What's Left to Build

### Potential Improvements (Not Yet Planned)
- [ ] Command-line argument parsing for runtime configuration
- [ ] More sophisticated schedule generation (not just random)
- [ ] Historical data analysis tools
- [ ] Better error recovery and retry mechanisms
- [ ] Unit tests for individual agents
- [ ] Integration tests for full workflow
- [ ] Docker containerization for easy deployment
- [ ] API endpoint for external schedule submission

## Current Status

### Project Phase
Dashboard UI improvements completed. All 3 schedule modes functional with preview workflow.

### Code Quality
- Dashboard callbacks verified, no duplicate output errors
- Preview workflow tested and working
- Responsive CSS media queries for mobile support

### Documentation Status
- [x] Memory Bank initialized with core files
- [x] Legacy docs removed
- [x] Plan documents created for major features
- [x] activeContext.md updated with current focus

## Recent Changes (2026-01-31)

### Dashboard UI Redesign
- Complete UI overhaul with modern professional light theme
- Two tabs: Schedule Configuration and Status & Plots
- Preview workflow with diff visualization
- Accept/Clear buttons for schedule changes
- Fixed duplicate callback outputs error

### Preview Workflow Implementation
- Random Mode: Configure start/end/step → Preview → Accept
- CSV Mode: Upload file → Adjust start date/time → Preview updates → Accept
- API Mode: Enter password → Connect & Fetch
- Diff visualization: Existing (dashed gray) vs Preview (solid blue fill)

### CSS Styling
- Color palette: Blue (#2563eb), Green (#16a34a), Red (#dc2626)
- Uniform spacing scale (4px, 8px, 12px, 16px, 24px)
- Standardized border radius (6px-8px)
- Responsive breakpoints for mobile devices
