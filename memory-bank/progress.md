# Progress: HIL Scheduler

## What Works

### Core Agents (New Architecture)
- [x] **Director Agent** (`hil_scheduler.py`): Updated shared data structure with two schedules
- [x] **Data Fetcher Agent** (`data_fetcher_agent.py`): **REWRITTEN** - Decoupled API-only fetcher
- [x] **Scheduler Agent** (`scheduler_agent.py`): **UPDATED** - Reads active_schedule_source to choose schedule
- [x] **Plant Agent** (`plant_agent.py`): Merged PPC + Battery functionality (unchanged)
- [x] **Measurement Agent** (`measurement_agent.py`): Logs data to CSV (unchanged)
- [x] **Dashboard Agent** (`dashboard_agent.py`): **REWRITTEN** - Three-tab structure

### New Architecture (2026-02-01)
- [x] **Two Shared Schedules**: `manual_schedule_df` and `api_schedule_df`
- [x] **Active Source Selector**: `active_schedule_source` ('manual' or 'api')
- [x] **Decoupled Data Fetcher**: No polling, just fetches API when password is set
- [x] **Manual Schedule Manager** (`manual_schedule_manager.py`): Simple utility module

### Schedule Management
- [x] **Manual Schedule**: Random generation and CSV upload via dashboard
- [x] **API Schedule**: Fetched independently by Data Fetcher agent
- [x] **Smart Replacement**: New data replaces only overlapping periods
- [x] **asof() Lookup**: Scheduler uses pandas asof() for robust time-based lookup

### Dashboard UI (Three-Tab Structure)
- [x] **Tab 1: Manual Schedule** - Random generation, CSV upload, preview/accept
- [x] **Tab 2: API Schedule** - Password input, connection status, API schedule preview
- [x] **Tab 3: Status & Plots** - Active source selector, live graphs, system status
- [x] Modern professional light theme with clean white surfaces
- [x] Schedule preview with diff visualization (existing vs preview)
- [x] Responsive design with CSS media queries

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

## Recent Changes (2026-02-01)

### Major Architecture Refactoring
Split schedule management into two independent schedules:

**New Components:**
- `manual_schedule_df` - Managed directly by dashboard
- `api_schedule_df` - Managed by decoupled Data Fetcher agent
- `active_schedule_source` - Selects which schedule the scheduler uses
- `manual_schedule_manager.py` - Simple utility for random/CSV operations

**Rewritten Agents:**
- **Data Fetcher**: Simple loop, no mode polling, just fetches API when password set
- **Dashboard**: Three-tab structure (Manual, API, Status & Plots)
- **Scheduler**: Reads `active_schedule_source` to choose schedule

### Schedule Creation Simplification (Earlier)
- Merged Random Schedule and CSV Upload into single "Manual" mode
- Mode selector now shows 2 options: Manual | API

## Previous Changes (2026-01-31)

### Dashboard UI Redesign
- Complete UI overhaul with modern professional light theme
- Two tabs: Schedule Configuration and Status & Plots
- Preview workflow with diff visualization
- Accept/Clear buttons for schedule changes
- Fixed duplicate callback outputs error

### Preview Workflow Implementation
- Manual → Random: Configure start/end/step → Preview → Accept
- Manual → CSV: Upload file → Adjust start date/time → Preview updates → Accept
- API Mode: Enter password → Connect & Fetch
- Diff visualization: Existing (dashed gray) vs Preview (solid blue fill)

### CSS Styling
- Color palette: Blue (#2563eb), Green (#16a34a), Red (#dc2626)
- Uniform spacing scale (4px, 8px, 12px, 16px, 24px)
- Standardized border radius (6px-8px)
- Responsive breakpoints for mobile devices
