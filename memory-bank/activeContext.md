# Active Context: HIL Scheduler

## Current Focus (Now)
1. Keep memory bank and audit artifacts aligned with the current dual-plant runtime and refactor outcomes.
2. Maintain robust plant control safety (per-plant transitions, guarded transport switching, and confirmation-gated fleet actions).
3. Stabilize and validate dashboard time-window semantics:
 - Status tab should show only immediate context (current day + next day),
 - Plots tab remains the path for historical inspection.
4. Stabilize the new merged schedule dispatch model (API base + per-signal manual overrides) and the redesigned Manual Schedule editor UX, now split into dashboard-owned drafts + settings-engine activation/update commands.
5. Keep reliability guardrails green via automated regression tests and CI enforcement, including measurement compression, posting-gate semantics, and schedule-window pruning behavior.
6. Prepare follow-up hardening for remaining high-risk paths (posting durability, remote smoke coverage, queue topology tradeoffs/prioritization).

## Open Decisions and Risks
1. Control-engine command queue is serialized and bounded; long safe-stop/transport operations can delay later commands (UI queue/backlog visibility now exists, but per-plant queue topology remains a deferred design decision).
2. API posting durability remains in-memory only; pending queue is lost on process restart.
3. Logging retention policy is undefined; date-routed files accumulate without automatic pruning.
4. Operational validation gap remains for remote transport end-to-end flows.
5. Legacy compatibility aliases in `config_loader.py` are now opt-in; removal timeline for the fallback flag remains open.
6. Lock-discipline target is improved but not complete; high-value measurement cache paths were refactored, and lower-priority measurement/cache paths still need audit.
7. Historical dense measurement CSV files created while compression was inactive are intentionally not backfilled.
8. Historical plots tab currently rescans and reloads CSV files on demand; performance may degrade with very large `data/` directories.
9. Transition UX now combines immediate click-feedback overlay + server/runtime transition state + Modbus confirmation; hold-window tuning may need additional operator validation across remote latency conditions.
10. Modbus error strings are now surfaced to operators; message wording/aggregation may need refinement to avoid noisy UI on unstable links.
11. Manual schedule editor drafts are stored in shared runtime state (`manual_schedule_draft_series_df_by_key`), so concurrent dashboard sessions can overwrite each other's draft edits (per-session isolation deferred; single-operator assumption currently accepted).

## Rolling Change Log (Compressed, 30-Day Window)

### 2026-02-25
- Continued internal refactor cleanup without behavior changes:
  - extracted generic command lifecycle bookkeeping into `command_runtime.py` and reduced `control_command_runtime.py` / `settings_command_runtime.py` to thin wrappers,
  - extracted shared engine command-cycle execution bookkeeping into `engine_command_cycle_runtime.py` and refactored control/settings engines to reuse it,
  - refactored `measurement_agent.py` `flush_pending_rows()` to swap/process/merge pending rows with shorter lock hold times,
  - added focused integration wiring regressions (`tests/test_dashboard_engine_wiring.py`) covering intent helper -> enqueue -> engine single-cycle -> shared-state mutation for both control and settings paths,
  - reduced enqueue callback duplication in `dashboard_agent.py` by centralizing command enqueue/token/log helper logic.
- Completed compatibility-contract cleanup and concurrency hygiene pass:
  - retired compatibility-only shared-state keys `active_schedule_source`, `schedule_switching`, and `measurement_posting_enabled` from runtime init/consumers/tests,
  - `posting_runtime.policy_enabled` is now the only canonical runtime posting-policy source,
  - removed deprecated `schedule_manager.py` from the active repository after migration,
  - reduced `measurement_agent.py` lock hold time in aggregate cache rebuild and current-file cache upsert by moving pandas-heavy work outside `shared_data["lock"]`.
- Strengthened API runtime state authority and engine helper de-dup:
  - added `api_runtime_state.py` to centralize `api_connection_runtime` normalization/recompute and sub-health publication (`fetch_health`, `posting_health`),
  - `settings_engine_agent.py` now publishes connect/disconnect transitions and probe results through the shared API runtime helper,
  - `data_fetcher_agent.py` publishes fetch health (`ok` / `error` / `disabled`) into `api_connection_runtime.fetch_health`,
  - `measurement_agent.py` publishes posting health (`ok` / `error` / `idle` / `disabled`) into `api_connection_runtime.posting_health`,
  - dashboard API controls/status rendering now uses authoritative `api_connection_runtime.state` / `last_error` only (no dashboard-derived API `Error` state).
- Reduced engine status duplication:
  - added `engine_status_runtime.py` and refactored `control_engine_agent.py` + `settings_engine_agent.py` to reuse shared queue/active-command/failed-recent status publishing helpers.
- Added regression coverage for:
  - `api_runtime_state.py` state recompute/transition/error-clearing semantics,
  - `engine_status_runtime.py` queue metrics + active-command metadata,
  - fetcher/measurement/settings/shared-state tests updated for nested API sub-health runtime contract.
- Implemented second-pass UI separation for settings/manual/API paths:
  - added `settings_engine_agent.py` + `settings_command_runtime.py` with separate FIFO settings queue and command lifecycle tracking (`settings_command_*` keys),
  - added server-owned runtime state for manual series activation (`manual_series_runtime_state_by_key`), API connection (`api_connection_runtime`), and posting policy (`posting_runtime`),
  - added dashboard helper modules `dashboard_settings_intents.py` and `dashboard_settings_ui_state.py`.
- Manual Schedule tab behavior changed:
  - editor/load/save now writes dashboard-owned draft series (`manual_schedule_draft_series_df_by_key`) instead of directly mutating scheduler-applied manual series,
  - per-series controls are now command-driven `Activate` / `Inactivate` / `Update`,
  - per-series UI shows server-owned transition/runtime state via button labels (`Activate/Activating.../Active`, `Inactive/Inactivating...`) with short optimistic button feedback,
  - manual series plots now overlay `Staged (Editor)` vs `Applied (Server)` schedules to make resend/update differences visible,
  - redundant per-series status text lines were removed (buttons carry the activation state).
- API tab behavior changed:
  - `Set Password` button replaced with `Connect` semantics (use input password if present, otherwise stored password),
  - `Disconnect` now intentionally disconnects API runtime without clearing stored password,
  - posting enable/disable is settings-command driven with transition states and separate policy vs effective posting status display,
  - terminal button labels now show `Connected` / `Disconnected` on the API connect/disconnect pair.
- Runtime gating updates:
  - `measurement_agent.py` posting-effective gate now considers `posting_runtime` and `api_connection_runtime` (with backward-compatible fallback),
  - `data_fetcher_agent.py` respects intentional API disconnect via `api_connection_runtime` (with backward-compatible fallback).
- Added regression coverage for settings command runtime/engine and dashboard settings intent/UI helper behavior; full suite remains green (`125 tests`).
 - Follow-up refactor/test additions increased full-suite coverage count to `140` tests while preserving green status.
- Added Status-tab runtime health surfacing for operator visibility:
  - top-card control-engine summary (`alive`, active command, last finished command, last loop error),
  - top-card command queue summary (`queued`, `running`, recent failed/rejected count, backlog-high hint).
- Extended `plant_observed_state_by_plant` runtime schema with Modbus diagnostics:
  - `read_status` (`ok` / `connect_failed` / `read_error` / `unknown`),
  - `last_error` structured payload (`timestamp`, `code`, `message`),
  - `consecutive_failures`.
- Control engine now publishes `control_engine_status` in shared state each loop with queue metrics and runtime health metadata for UI consumption.
- Status-tab per-plant status UI now shows cached Modbus link condition, observed freshness age/stale marker, failure count, and last error message (no direct dashboard Modbus reads).
- Simplified Status-tab primary per-plant status line for operators:
  - renamed `State` -> `Plant State`,
  - removed internal `Scheduler gate` field,
  - removed redundant raw `Modbus enable` field from the primary line (detailed Modbus diagnostics remain below).
- Added pure formatting helpers in `dashboard_control_health.py` plus regression coverage for control-engine/queue and per-plant Modbus health summaries.
- Extended control-engine regression coverage for:
  - `control_engine_status` loop publishing (`last_loop_*`, `last_observed_refresh`, queue/running counts, last finished command),
  - command-crash `last_exception` publishing,
  - observed-state error classification/reset semantics.
- Implemented first-pass control-path UI/engine separation hardening:
  - added `control_engine_agent.py` to own start/stop/fleet/transport/record command execution and control-path Modbus I/O,
  - added bounded FIFO control command queue + lifecycle status tracking in shared state (`control_command_queue`, `control_command_status_by_id`, `control_command_history_ids`, `control_command_active_id`, `control_command_next_id`),
  - dashboard control callbacks now enqueue normalized command intents instead of executing control flows or spawning execution threads.
- Added cached plant observed-state publication (`plant_observed_state_by_plant`) in control engine (`enable`, `p_battery`, `q_battery`, freshness/error/stale metadata); Status tab no longer performs direct Modbus polling for control/status paths.
- Added pure dashboard trigger->command intent helpers (`dashboard_command_intents.py`) and targeted regression coverage for intent mapping.
- Added command runtime and control-engine regression coverage:
  - queue/lifecycle bookkeeping,
  - command execution ordering and idempotent record on/off behavior,
  - observed-state cache stale/failure behavior.
- Updated shared-state contract regression coverage for new command queue and observed-state keys.
- Fixed local runtime smoke-test fixture drift after scheduler/manual-series contract changes and normalized dashboard-plotting timezone assertions (Plotly timestamp serialization behavior).
- Refined dashboard control transition UX after hardening:
  - immediate click feedback for `starting`/`stopping` without waiting for enqueue success,
  - short forced transition overlay window (currently `2s`),
  - server/runtime `starting`/`stopping` persists until Modbus `enable` confirms `running`/`stopped`.
- Replaced runtime manual/API source switching dispatch behavior with merged dispatch:
  - API schedules are now the base,
  - manual overrides are stored as four independent series (`lib_p`, `lib_q`, `vrfb_p`, `vrfb_q`),
  - per-series active/inactive toggles control overwrite participation for `P`/`Q`.
- Added shared-state contract keys for manual override series and merge-enable flags:
  - `manual_schedule_series_df_by_key`,
  - `manual_schedule_merge_enabled_by_key`.
- Kept `manual_schedule_df_by_plant` as a derived compatibility/display cache rebuilt from manual series.
- Updated scheduler runtime semantics:
  - API staleness still zeros the API base values,
  - enabled manual overrides can still supply `P` and/or `Q` during API staleness,
  - manual override series are pruned on day rollover to local `current day + next day`.
- Removed dashboard schedule-source switch UI/callback flow (transport switch unchanged).
- Updated Status-tab schedule overlays to render the merged effective schedule instead of branching on source.
- Rebuilt Manual Schedule tab:
  - four always-visible manual override plots (`LIB/VRFB` x `P/Q`) with active/inactive toggles,
  - compact right-side breakpoint editor (responsive stacked on small screens),
  - relative breakpoint CSV save/load (`hours, minutes, seconds, setpoint`) for the selected series,
  - row-level add/delete controls and first-row `00:00:00` enforcement,
  - runtime/manual-series sanitization to local `current day + next day`.
- Removed random manual schedule generator from the active Manual tab workflow.
- Updated API measurement posting gate semantics:
  - runtime posting toggle + API password now control posting eligibility,
  - manual override usage no longer suppresses measurement posting.
- Added/updated targeted regression coverage for:
  - scheduler merged dispatch priority and stale-base/manual-override behavior,
  - shared-state contract keys,
  - measurement posting gate independent of `active_schedule_source`,
  - dashboard control test cleanup after source-switch helper removal.
- Performed iterative Manual editor UI refinements (compact selector/date/time/breakpoint rows, button-only CSV load control, responsive plots/editor balance, dropdown menu fixes, reduced visual clutter).

### 2026-02-24
- Fixed historical `Plots` tab range-slider default behavior:
  - `dashboard_history.clamp_epoch_range()` now treats fully out-of-domain selections (including the initial layout placeholder `[0, 1]`) as stale and defaults back to the full discovered history range,
  - prevents first-load collapsed selection at the left edge and empty-looking historical plots.
- Added/extended `tests/test_dashboard_history.py` clamp-range regression coverage for:
  - stale placeholder below-domain selection,
  - fully above-domain selection,
  - partial overlap clamping behavior.
- Compacted the `Plots` tab historical availability timeline (`LIB` / `VRFB`) in `dashboard_agent.py` by reducing figure height/margins and lowering legend placement so the bars render closer together with less vertical whitespace.
- Bounded API schedule runtime retention in `data_fetcher_agent.py` to the local calendar window `[today 00:00, day+2 00:00)` so `api_schedule_df_by_plant` no longer grows indefinitely across days.
- Updated API `today` fetch writes to merge with existing in-window rows (instead of blind overwrite) so previously fetched tomorrow rows are preserved during same-day retries.
- Constrained Status-tab plots (all sources: `manual` and `api`) to the same local `current day + next day` window via optional plot-helper x-window filtering.
- Added regression coverage for:
  - API schedule pruning/retention and bounded tomorrow merges in `tests/test_data_fetcher_agent.py`,
  - plot-helper schedule/measurement x-window cropping and boundary semantics in `tests/test_dashboard_plotting.py`.
- Added local-mode plant-start SoC restore from persisted measurements:
  - dashboard start flow now looks up the latest on-disk non-null `soc_pu` for the target plant from `data/YYYYMMDD_<plant>.csv`,
  - falls back to `STARTUP_INITIAL_SOC_PU` when no persisted SoC is available,
  - local-only behavior (remote transport start path unchanged).
- Added explicit dashboard->plant-agent local emulator SoC seed handshake in shared state:
  - `local_emulator_soc_seed_request_by_plant`,
  - `local_emulator_soc_seed_result_by_plant`.
- Updated `plant_agent.py` to apply SoC seed requests only while the local emulator plant is disabled (guard against mid-run SoC resets), then acknowledge `applied|skipped|error`.
- Added regression coverage for:
  - latest persisted SoC lookup helper behavior (latest row selection, null-boundary ignore, suffix filtering, clamping),
  - plant-agent seed request handling (`applied` when disabled, `skipped` when enabled),
  - shared-state contract keys for the new seed request/ack maps.

### 2026-02-23
- Implemented unit-aware Modbus point conversions on top of binary codec:
  - point `unit` now drives conversions between external Modbus engineering values and internal runtime units,
  - supported units include SoC (`pc`/`%`/`pu`), active/reactive power (`W/kW/MW`, `var/kvar/Mvar`), and voltage (`V/kV`).
- Performed internal voltage migration from per-unit to absolute kV:
  - runtime/measurement field renamed `v_poi_pu` -> `v_poi_kV`,
  - API posting voltage now uses `v_poi_kV * 1000` (no pu reconstruction),
  - compression tolerance key renamed to `recording.compression.tolerances.v_poi_kV`,
  - plant model nominal voltage renamed `poi_voltage_v` -> `poi_voltage_kv`.
- Implemented Modbus point-schema refactor (breaking config migration):
  - replaced endpoint `registers` integer maps with structured `points` metadata in `config.yaml`,
  - required explicit endpoint `byte_order` / `word_order` for every `local`/`remote` Modbus endpoint (no defaults),
  - added shared `modbus_codec.py` for holding-register encode/decode (supports `int16`/`uint16`/`int32`/`uint32`/`float32`),
  - refactored scheduler, measurement sampling, dashboard Modbus helpers, and local plant emulation to use shared codec + normalized point specs,
  - runtime resolver now exposes endpoint ordering + `points`,
  - preserved current on-wire behavior for existing P/Q and SoC points (P/Q int16 hW, `soc` `/10000`) and retained optional `start_command` / `stop_command` point definitions (voltage semantics were changed later the same day in the kV migration).
- Added regression coverage for:
  - Modbus codec roundtrips/overflow/quantization and float32 ordering behavior,
  - config-loader point-schema normalization, required endpoint ordering, and legacy `registers` rejection.
- Diagnosed CI failure source as a brittle config-loader regression test (`tests/test_config_loader_recording_compression.py`) that pinned `recording.compression.max_kept_gap_s` to `3600.0` despite `config.yaml` using `360`.
- Relaxed the compression-gap config-loader test to validate contract shape (present, non-negative, float) instead of enforcing a specific configured value, so operator tuning of `max_kept_gap_s` does not break CI.
- Verified full test suite passes in the project virtualenv (`venv/bin/python -m unittest discover -s tests -v`); earlier pandas-related skips were due to using system `/usr/bin/python3` instead of repo `venv`.
- Hardened API schedule fetcher observability and next-day polling gate:
  - renamed config key `istentore_api.poll_start_time` -> `istentore_api.tomorrow_poll_start_time` (breaking change, no compatibility alias),
  - `config_loader.py` now normalizes `tomorrow_poll_start_time` to `HH:MM` and rejects legacy key usage,
  - `data_fetcher_agent.py` now compares next-day poll gate time numerically (fixes fragile string comparison for values like `9:00`),
  - added explicit fetch-attempt logs with `purpose=today|tomorrow`, local request window, and trigger reason,
  - added throttled tomorrow-gate state logs (`waiting` / `eligible`),
  - fixed partial `tomorrow` fetch status/logging to preserve retryable partial writes while surfacing window-specific error messages.
- Added regression coverage for:
  - `tomorrow_poll_start_time` parsing/normalization and legacy-key rejection in `config_loader.py`,
  - data fetcher next-day gate timing, partial/complete tomorrow fetch handling, and rollover promotion behavior (environment-dependent on local pandas install).
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
  - Stage C legacy-path cleanup progressed: `schedule_manager.py` removed after migration to `manual_schedule_manager.py` / `schedule_runtime.py`; legacy config aliases remain gated behind `HIL_ENABLE_LEGACY_CONFIG_ALIASES=1`.
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
