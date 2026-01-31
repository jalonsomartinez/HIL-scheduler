# Active Context: HIL Scheduler

## Current Focus
Dashboard UI improvements with modern light theme, tabbed interface, and preview workflow.

## Recent Changes (2026-01-31)

### Dashboard UI Redesign
- Complete UI overhaul with professional light theme
- Two tabs: Schedule Configuration and Status & Plots
- Preview workflow: Generate preview → Review diff → Accept to commit
- Fixed duplicate callback outputs error

### New Dashboard Features
1. **Tab Structure**:
   - Tab 1: Schedule Configuration - Mode selection, controls, preview
   - Tab 2: Status & Plots - Real-time status, control buttons, live graphs

2. **Schedule Preview Workflow**:
   - Random Mode: Configure start/end/step → Preview → Accept
   - CSV Mode: Upload file → Adjust start date/time → Preview updates → Accept
   - API Mode: Enter password → Connect & Fetch → Schedule loaded
   - Diff visualization: Existing schedule (dashed gray) vs Preview (solid blue)
   - Accept button commits preview to active schedule
   - Clear button removes preview only

3. **UI Design System**:
   - Color palette: Blue (#2563eb), Green (#16a34a), Red (#dc2626)
   - Clean white surfaces with subtle borders
   - Uniform spacing scale (4px-24px)
   - Responsive CSS media queries

### Fixed Issues
- Duplicate callback outputs error (allow_duplicate=True added)
- CSV upload now uses preview workflow instead of immediate load
- Removed conflicting callback inputs

## Next Steps
- Test all preview workflows end-to-end
- Consider adding CSV validation before preview
- Consider adding schedule validation warnings

## Architecture Notes

### Preview Workflow Pattern
```
1. User configures mode controls
2. Preview callback generates temporary DataFrame
3. Data stored in dcc.Store (preview-schedule)
4. Graph callback reads from store and shows diff
5. Accept callback: Reads from store → Commits to schedule
6. Clear callback: Sets store to None
```

### Key Dashboard Files
- [`dashboard_agent.py`](dashboard_agent.py): Main dashboard with all callbacks
- [`assets/custom.css`](assets/custom.css): Light theme styles
