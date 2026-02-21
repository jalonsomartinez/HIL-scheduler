# Active Context: HIL Scheduler

## Current Focus (Now)
1. Keep memory bank aligned with the dual-plant runtime contract and avoid stale schema drift.
2. Maintain robust plant control safety (start/stop transitions and guarded global switches).
3. Stabilize logging and dashboard observability behavior (date-routed log files and live "Today" log view) while preserving control callback safety.
4. Improve confidence through targeted automated tests around scheduler gating, recording boundaries, and API posting retry behavior.

## Open Decisions and Risks
1. Test coverage gap: no focused regression suite yet for core control contracts.
2. API posting durability: retry queue is in-memory and is lost on restart.
3. Logging retention policy: file output is date-routed per configured timezone without automatic historical pruning.
4. Operational validation gap: limited scripted end-to-end verification for remote transport behavior.
5. No automated visual regression checks yet for dashboard CSS/class-hook changes.

## Rolling Change Log (Compressed, 30-Day Window)

### 2026-02-21
- Implemented timezone-aware date-routed logging in `logger_config.py`:
  - each log record writes to `logs/YYYY-MM-DD_hil_scheduler.log` based on record timestamp date,
  - active file switch updates `shared_data["log_file_path"]`.
- Updated dashboard logs UX:
  - dropdown default/value changed from `current_session` to `today`,
  - top option relabeled to `Today`,
  - logs view reads the tail of today's file for live updates and keeps historical file browsing.
- Fixed dashboard logs callback regression introduced during logs refactor:
  - replaced incorrect `now_tz(tz)` call with timezone-aware `datetime.now(tz)` in today-file path helper.
- Reworked dashboard presentation layer to a tokenized CSS system aligned with i-STENTORE branding.
- Refactored dashboard layout styling hooks:
  - branded header block and logo integration,
  - class-based tab styling,
  - class-based modal/logs/posting-card styling (reduced inline styles).
- Added local font assets for dashboard UI rendering:
  - `assets/brand/fonts/DMSans-Regular.ttf`,
  - `assets/brand/fonts/DMSans-Bold.ttf`,
  - `assets/brand/fonts/OFL.txt`.
- Updated Plotly figure presentation through shared theme helpers while preserving existing `uirevision` and callback behavior.
- Applied operator-requested visual refinements:
  - green-only background treatment and later flat corporate-green page background,
  - non-signature logo variant,
  - stronger toggle selected-state contrast,
  - flat (non-gradient) green/red button styling.

### 2026-02-20
- Updated Istentore API auth-retry policy to treat `403` like `401` for token renewal.
- Bounded schedule fetch auth recovery to a single re-auth retry to avoid unbounded recursion.
- Kept measurement post auth recovery as one retry, now triggered by either `401` or `403`.

### 2026-02-19
- Completed dual-plant runtime consolidation across config, scheduler, measurement, dashboard, and fetcher.
- Restored dashboard safety/observability features after migration:
  - logs tab,
  - source switch confirmation and safe-stop flow,
  - per-plant transition-aware controls,
  - stable plot `uirevision` behavior.
- Added measurement posting observability state and API-tab summaries per plant.
- Hardened posting payload conversion/validation and queue attribution metadata.

### 2026-02-18
- Added deterministic today/tomorrow rollover reconciliation in fetcher status.
- Added stale API setpoint cutoff in scheduler and immediate-start setpoint selection path.
- Introduced anchored monotonic measurement step scheduling to remove trigger drift.
- Added independent API measurement post cadence with bounded retry queue and backoff.

### 2026-02-17
- Standardized timezone-aware schedule and measurement handling across agents.
- Refactored recording to daily per-plant files with timestamp-based row routing and cache-backed plots.
- Decoupled dispatch control from recording control while preserving safe-stop semantics.

### 2026-02-02
- Improved dashboard state transitions with clearer intermediate states and faster refresh cadence.
- Added dashboard logs experience for live session stream and historical file viewing.
- Resolved initialization behavior for startup selector state loading.

### 2026-02-01
- Refactored schedule architecture to separate manual and API schedule maps.
- Simplified data fetcher polling/backoff behavior.
- Reduced lock contention in scheduler/measurement/dashboard paths for responsiveness.
