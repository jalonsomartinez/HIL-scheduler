# Active Context: HIL Scheduler

## Current Focus (Now)
1. Keep memory bank and audit artifacts aligned with the current dual-plant runtime and refactor outcomes.
2. Maintain robust plant control safety (per-plant transitions, guarded global switches, and confirmation-gated fleet actions).
3. Stabilize and validate the new historical `Plots` tab (timeline/range browsing + CSV/PNG exports) against real operator datasets.
4. Keep reliability guardrails green via automated regression tests and CI enforcement, including measurement compression and posting-gate semantics.
5. Prepare follow-up hardening for remaining high-risk paths (dashboard synchronous Modbus polling, posting durability, remote smoke coverage).

## Open Decisions and Risks
1. Dashboard interval callbacks still perform synchronous Modbus reads; remote endpoint slowness can degrade responsiveness.
2. API posting durability remains in-memory only; pending queue is lost on process restart.
3. Logging retention policy is undefined; date-routed files accumulate without automatic pruning.
4. Operational validation gap remains for remote transport end-to-end flows.
5. Legacy compatibility aliases in `config_loader.py` are now opt-in; removal timeline for the fallback flag remains open.
6. Lock-discipline target is not fully met in measurement cache paths where dataframe operations still occur under lock.
7. Historical dense measurement CSV files created while compression was inactive are intentionally not backfilled.
8. Historical plots tab currently rescans and reloads CSV files on demand; performance may degrade with very large `data/` directories.

## Rolling Change Log (Compressed, 30-Day Window)

### 2026-02-23
- Renamed per-plant Modbus register map setpoint keys from `p_setpoint_in` / `q_setpoint_in` to canonical `p_setpoint` / `q_setpoint` across runtime agents, config, and tests.
- Updated `config_loader.py` register normalization to accept legacy `*_in` setpoint keys as backward-compatible input aliases while emitting canonical runtime register maps.
- Moved local-emulation startup SoC config from per-plant `plants.*.model.initial_soc_pu` to shared `startup.initial_soc_pu`.
- Updated config-loader runtime contract to expose `STARTUP_INITIAL_SOC_PU` and removed `initial_soc_pu` from normalized `PLANTS[*].model`.
- Kept opt-in legacy alias compatibility (`PLANT_INITIAL_SOC_PU`) by sourcing it from `STARTUP_INITIAL_SOC_PU`.
- Updated local plant emulator startup initialization to apply one shared startup SoC for both plants.
- Added config-loader regression coverage for shared startup SoC parsing, plant-model schema removal, and alias mapping.
- Added configurable measurement compression keep-gap threshold `recording.compression.max_kept_gap_s` (flattened as `MEASUREMENT_COMPRESSION_MAX_KEPT_GAP_S`; default `3600s`).
- Updated measurement compression semantics so both tolerance comparison and keep-gap comparison are anchored to the last kept real row (not the last raw sample), preventing drift in long stable runs.
- Added/updated regression coverage for:
  - keep-gap retention when stable samples exceed the configured interval,
  - drift prevention by comparing against the last kept row,
  - config-loader parsing of the new compression key.
  - scheduler setpoint-register access updated to canonical keys in source-switch regression coverage.

### 2026-02-22
- Renamed dashboard `Status & Plots` tab to `Status` (functionality unchanged for live controls/status plots).
- Added new dashboard `Plots` tab for historical measurement browsing:
  - scans `data/*.csv` for known plant files (`lib`, `vrfb`) using sanitized filename suffix matching,
  - builds a full-range timeline across discovered measurement timestamps,
  - exposes a range slider (epoch-ms backed, timezone-formatted labels),
  - renders per-plant historical measurement plots using the shared plant-figure helper (measurements only; no schedule overlay).
- Added per-plant historical export actions:
  - cropped CSV download for selected range,
  - PNG export of current graph via client-side Plotly download (no `kaleido` dependency).
- Added `dashboard_history.py` helper module for history scan/index, range clamping, crop loading, and CSV serialization.
- Added `tests/test_dashboard_history.py` coverage for helper behaviors (scan mapping, clamp logic, inclusive crop, serialization, slider marks).

### 2026-02-21
- Added dashboard UI/operator-control updates:
  - `Start All` / `Stop All` actions in Status top card with dedicated confirmation modal,
  - removed redundant `Source | Transport` status line from top card,
  - API-tab runtime posting toggle (`Enabled`/`Disabled`) for read-only API-mode testing,
  - modal `Cancel` buttons now red,
  - recording stop buttons now red.
- Applied additional UI layout refinements for flatter/readability-focused operator view:
  - page background switched to white,
  - reduced header vertical padding,
  - menu-like tab strip styling (no outer card shell),
  - tab-content cards now align to tab width (removed side margins),
  - mobile `Start All` / `Stop All` kept on one row,
  - overall style flattened with minimal radius and reduced depth.
- Expanded Status-tab API inline text to show both today and tomorrow per-plant fetched-point counts.
- Added runtime posting control contract:
  - `shared_data["measurement_posting_enabled"]` initialized from `ISTENTORE_POST_MEASUREMENTS_IN_API_MODE`,
  - measurement posting gate now evaluates runtime toggle + API source + password.
- Added regression coverage for runtime posting toggle-off behavior in `tests/test_measurement_posting_telemetry.py`.
- Adjusted page background to white per operator UX request.
- Completed staged cleanup plan across Stage A/B/C:
  - Stage A shared helper extraction (`runtime_contracts.py`, `schedule_runtime.py`, `shared_state.py`).
  - Stage B concern split for dashboard and measurement helpers/modules.
  - Stage C legacy-path deprecation: `schedule_manager.py` marked deprecated and legacy config aliases gated behind `HIL_ENABLE_LEGACY_CONFIG_ALIASES=1`.
- Fixed Stage B regressions:
  - restored measurement recording start path (`sanitize_plant_name` import in `measurement_agent.py`),
  - fixed logs parsing in `dashboard_logs.py` regex.
- Added regression test suite under `tests/` covering:
  - logs parsing/today-file behavior,
  - measurement record-start boundary behavior,
  - local runtime start/record/stop smoke flow,
  - scheduler manual->API stale zero-dispatch behavior,
  - measurement posting telemetry failure->recovery behavior,
  - measurement posting queue maxlen overflow behavior,
  - dashboard safe-stop/source-switch/transport-switch control flows.
- Added CI workflow (`.github/workflows/ci.yml`) running compile + unittest checks.
- Restored measurement compression behavior in active runtime:
  - `recording.compression.enabled` and `recording.compression.tolerances.*` are now applied in `measurement_agent.py`,
  - stable runs keep first/latest points while null boundaries remain explicit,
  - non-force flush retains one tail row per active recording file to preserve continuity.
- Added regression tests in `tests/test_measurement_compression.py` for:
  - compression-enabled stable run compaction,
  - compression-disabled full-row persistence,
  - periodic flush continuity with retained mutable tail.
- Extracted dashboard control flow helpers to `dashboard_control.py` and wired `dashboard_agent.py` to shared safe-stop/switch helpers.
- Added explicit `build_initial_shared_data(config)` contract constructor in `hil_scheduler.py` plus schema regression tests.
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
  - green-only background treatment, then flat corporate-green page background, and finally white page background,
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
