# Active Context: HIL Scheduler

## Current Focus
Simplified schedule creation with merged Manual mode containing Random and CSV options.

## Recent Changes (2026-02-01)

### Schedule Creation Simplification
Merged Random Schedule and CSV Upload into a single "Manual" mode:
- **Before**: 3 mode options (Random, CSV, API)
- **After**: 2 mode options (Manual, API)
- Manual mode contains sub-selector: Random Schedule | CSV Upload
- Same preview/accept workflow preserved for both sub-methods

### Technical Changes
1. **dashboard_agent.py**:
   - Mode selector reduced to 2 options: `['manual', 'api']`
   - Added `manual-sub-mode` radio for Random/CSV selection
   - Merged control sections into single Manual mode card
   - Updated all callbacks to handle new structure

2. **schedule_manager.py**:
   - Added `MANUAL` to `ScheduleMode` enum
   - Accept callback sets mode to `MANUAL` when schedule accepted

3. **assets/custom.css**:
   - Added `.sub-mode-selector` styles for the sub-method buttons

## Previous Changes (2026-01-31)

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
   - Manual → Random: Configure start/end/step → Preview → Accept
   - Manual → CSV: Upload file → Adjust start date/time → Preview → Accept
   - API Mode: Enter password → Connect & Fetch → Schedule loaded
   - Diff visualization: Existing schedule (dashed gray) vs Preview (solid blue)
   - Accept button commits preview to active schedule
   - Clear button removes preview only

3. **UI Design System**:
   - Color palette: Blue (#2563eb), Green (#16a34a), Red (#dc2626)
   - Clean white surfaces with subtle borders
   - Uniform spacing scale (4px-24px)
   - Responsive CSS media queries

## Next Steps
- Test Manual mode with both Random and CSV sub-options
- Verify API mode still works correctly
- Consider adding CSV validation before preview

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
