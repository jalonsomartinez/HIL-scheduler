# Progress: HIL Scheduler

## Working Now
1. Dual logical plants (`lib`, `vrfb`) run under a shared transport model with merged schedule dispatch (API base + manual overrides), plus independent per-plant dispatch-send and recording gates.
2. Scheduler dispatches per plant from a merged effective schedule (API base + enabled manual per-signal overrides) and applies API stale-setpoint guardrails to the API base.
 - Normalized plant Modbus endpoints now expose required connection ordering (`byte_order`, `word_order`) plus structured holding-register `points` metadata (address/format/access/unit/scale).
 - Scheduler/measurement/dashboard/local-emulation Modbus I/O uses shared codec helpers (`modbus/codec.py`) instead of ad-hoc scaling/bit conversions.
 - API schedule runtime maps are now pruned to local `current day + next day` retention in the fetcher to prevent unbounded growth across day rollovers.
 - Manual override runtime series are stored as four independent series (`lib_p`, `lib_q`, `vrfb_p`, `vrfb_q`) with per-series merge toggles and are pruned to the same local two-day window.
 - Scheduler setpoint write deduping now preserves retry behavior on Modbus write failure (failed writes are not cached as sent).
 - Scheduler dispatch write decisions now reconcile against plant setpoint readback (`p_setpoint`/`q_setpoint`) using register-exact compare, correcting external drift even when the target value is unchanged.
 - If scheduler readback fails for a point, write/no-write falls back to local last-success cache dedupe for that point (preserving no-spam behavior while keeping retry semantics).
3. Local emulation runs both plant Modbus servers concurrently with SoC and power-limit behavior.
 - Local emulation startup SoC is configured once via `startup.initial_soc_pu` and applied to both plants.
 - On local plant start, dashboard now attempts to seed emulator SoC from the latest persisted on-disk measurement (`soc_pu`) for that plant, with fallback to `startup.initial_soc_pu`.
 - Local SoC seeding uses an explicit shared-state request/ack handshake so `plant_agent.py` updates internal emulator state (`soc_kwh`) before enable, not just the Modbus `soc` register.
4. Measurement pipeline provides:
- anchored sampling timing,
- per-plant daily recording,
- tolerance-based compression for stable measurement runs with explicit session boundaries,
- configurable keep-gap retention (`recording.compression.max_kept_gap_s`) and last-kept-row comparison anchoring to prevent drift,
- in-memory plot cache,
- API measurement posting with retry/backoff, per-plant telemetry, and token re-auth retry on `401`/`403`.
 - Modbus point `unit` is now applied during read/write conversions (SoC `pc|pu`, power `W/kW/MW` and `var/kvar/Mvar`, voltage `V/kV`).
 - Internal voltage is now `kV` end-to-end (`v_poi_kV` in measurement rows/CSV); API voltage posting uses `v_poi_kV * 1000`.
 - Plant model nominal voltage is now `plants.*.model.poi_voltage_kv`.
 - API schedule fetcher next-day polling gate uses normalized `istentore_api.tomorrow_poll_start_time` (flattened `ISTENTORE_TOMORROW_POLL_START_TIME`) with explicit today/tomorrow fetch intent logging and partial-window warning/error visibility.
5. Dashboard provides:
- per-plant Start/Stop + Record/Stop controls,
- top-card `Start All` / `Stop All` controls with confirmation modal for high-impact actions,
- transport switching with confirmation and safe-stop,
- control-path UI/engine separation for start/stop/record/fleet/transport actions:
  - dashboard callbacks enqueue normalized control intents instead of executing Modbus/control flows,
  - `control/engine_agent.py` serially executes commands and owns control-path Modbus I/O,
  - shared command lifecycle status/history is tracked in bounded shared-state maps/queue,
- cached plant observed-state publication (`enable`, `p_battery`, `q_battery`, stale/error metadata) for Status-tab control/status rendering (no direct dashboard Modbus polling on those paths),
- independent per-plant dispatch-send toggles (`Sending` / `Paused`) in Status cards, command-driven through the control engine (`plant.dispatch_enable` / `plant.dispatch_disable`) and mapped to `scheduler_running_by_plant`,
- physical plant-state cache (`plant_operating_state_by_plant`) derived from observed Modbus enable state and shown separately from control transition state,
- per-plant dispatch write status cache (`dispatch_write_status_by_plant`) with latest attempted/successful P/Q, timestamp, source, status, and error, rendered in Status cards as sent-setpoint observability,
- scheduler-originated dispatch status now carries readback reconciliation telemetry in `last_scheduler_context`, and the existing dispatch status line shows a compact inline `RB P/Q=...` hint when the latest attempt source is `scheduler`,
- Status-tab health surfacing for server/runtime conditions:
  - top-card control-engine summary (liveness, active command, last finished command, last loop error),
  - top-card queue summary (queued/running/recent failed, backlog-high hint),
  - per-plant Modbus diagnostics (link state/read error/freshness age/failure count/last error),
  - primary plant status line now separates physical plant state, control transition state, and recording status; dispatch send state and last write status/PQ are shown in dedicated lines below,
- immediate click-feedback transition overlay (`starting`/`stopping`) followed by server-owned transition state and Modbus-confirmed `running`/`stopped` (dispatch send state is separate and independently toggleable),
- `Status` tab (formerly `Status & Plots`) live status + control plots,
- `Plots` tab historical measurement browsing from `data/*.csv` with full-range timeline + range slider,
- `Plots` tab range slider now defaults to the full detected history span when the current value is a stale/placeholder out-of-domain range (avoids first-load collapsed selection),
- compacted `Plots` tab availability timeline (`LIB`/`VRFB`) to reduce vertical whitespace while preserving the top-range overview,
- per-plant historical exports (cropped CSV and client-side PNG),
- API-tab runtime posting toggle (`Enabled`/`Disabled`) for read-only tests,
- Manual Schedule tab redesign with four always-visible manual override plots and a compact breakpoint editor for one selected series at a time,
- dashboard-owned manual draft series plus per-series command-driven `Activate` / `Inactivate` / `Update` controls (server-owned activation state),
- manual per-series controls now use a true two-button activation toggle + separate `Update` button, with stateful button labels and no redundant per-series status text,
- manual per-series plots overlay staged editor schedule and applied server schedule for resend/update visibility,
- relative-row manual override CSV save/load (`hours, minutes, seconds, setpoint`) with terminal `end` row support,
- forced terminal `end` row for any non-empty manual editor schedule, displayed as `end` but stored/sent as a terminal duplicate-value numeric row,
- manual editor row times auto-clamped forward to enforce a minimum `60s` gap (including terminal `end` row), and empty-series default editor start time rounds up to the next local `10-minute` boundary,
- first manual breakpoint row no longer renders a delete button and delete requests for row 0 are rejected server-side,
- API tab now uses command-driven `Connect` / `Disconnect` and posting enable/disable transitions (settings engine), with API connection state decoupled from password storage and posting policy shown separately from effective posting status; connect/disconnect buttons show terminal labels `Connected` / `Disconnected`,
- API connection runtime `Error` state is now runtime-owned and published from agent health (`fetch_health` + `posting_health`) into `api_connection_runtime`; dashboard renders API state/error directly from that runtime contract,
- API status and posting health, including inline today/tomorrow per-plant fetch counts in Status tab,
- Status-tab plots intentionally show only local current-day + next-day schedule/measurement data (immediate context) and now render the merged effective schedule; historical inspection stays on `Plots`,
- shared plant plots (Status + historical `Plots`) now include a dedicated voltage subplot (`v_poi_kV`), while Status plots also render a vertical dashed current-time indicator,
- historical `Plots` tab plant figures now show recorded P/Q setpoints from measurement rows when no schedule dataframe is supplied (with duplicate-prevention when schedule traces are present),
- low-voltage plant voltage axes (nominal `< 10 kV`) now use explicit y-padding equal to `5%` of configured nominal voltage; higher-voltage plants use Plotly autorange,
- logs tab with live `Today` (current date file tail) and selectable historical files,
- branded UI theme (tokenized CSS, local font assets, flatter visual treatment, minimal corner radius, menu-style tab strip, full-width tab content cards, white page background).
- Balanced package layout is now in place for active runtime modules (`dashboard/`, `control/`, `settings/`, `measurement/`, `scheduling/`, `modbus/`, `runtime/`) while `hil_scheduler.py` remains the root launcher.
- Dashboard explicitly pins Dash `assets_folder` to repo-root `assets/`, and dashboard log helpers resolve repo-root `logs/` even when called from the `dashboard/` package directory.
- Shared repo-root path helpers now live in `runtime/paths.py`; dashboard/logging/control-engine paths use them for `assets/`, `logs/`, and key `data/` path generation.
- `api-docs-examples/README.md` now marks that folder as legacy/reference material (not active runtime code).
6. Automated validation now includes:
- module compile checks (`python3 -m py_compile *.py dashboard/*.py control/*.py settings/*.py measurement/*.py scheduling/*.py modbus/*.py runtime/*.py`),
- unit/smoke regression suite (`python -m unittest discover -s tests -v`, `170` tests in latest full run),
- CI execution via `.github/workflows/ci.yml`.
 - targeted historical-plots helper unit tests in `tests/test_dashboard_history.py` (environment-dependent on local pandas install).
 - `tests/test_dashboard_history.py` now explicitly covers stale-placeholder and fully out-of-domain slider-range defaulting semantics.
 - targeted measurement compression regressions covering keep-gap retention and last-kept-row drift prevention.
 - targeted config-loader regressions covering shared startup SoC parsing and legacy alias mapping.
 - targeted config-loader regression coverage for `tomorrow_poll_start_time` normalization and legacy `poll_start_time` rejection.
 - targeted data fetcher regression coverage for next-day gate timing, partial/complete tomorrow fetch status, and rollover promotion (environment-dependent on local pandas install).
 - targeted data fetcher regression coverage for API schedule pruning/retention and bounded tomorrow merges.
 - targeted plot-helper regression coverage for status-window x-range cropping, voltage subplot/time-indicator behavior, historical setpoint fallback, and voltage-axis padding in `tests/test_dashboard_plotting.py` (environment-dependent on local pandas install).
 - compression-gap config-loader regression now validates schema/typing (not a fixed `max_kept_gap_s` value) so config tuning does not cause CI failures.
- targeted measurement-storage SoC lookup regressions (`tests/test_measurement_storage_latest_soc.py`) and plant-agent local SoC seed request regressions (`tests/test_plant_agent_soc_seed_requests.py`).
- targeted scheduler merged-dispatch regressions (`tests/test_scheduler_source_switch.py`) covering manual override priority and stale API base fallback behavior.
- targeted manual end-row/terminal-duplicate encoding regressions (`tests/test_manual_schedule_manager_end_rows.py`, `tests/test_schedule_runtime_end_times.py`) covering CSV roundtrip, auto-sanitized gaps, and end-cutoff merge behavior.
- targeted posting telemetry regression coverage confirming posting gate no longer depends on `active_schedule_source`.
- targeted command-runtime/control-engine regressions (`tests/test_control_command_runtime.py`, `tests/test_control_engine_agent.py`) and dashboard intent/UI-state/control-health helper regressions (`tests/test_dashboard_command_intents.py`, `tests/test_dashboard_ui_state.py`, `tests/test_dashboard_control_health.py`).
- generic command-runtime helper extraction (`runtime/command_runtime.py`) keeps control/settings command-runtime wrappers thin and aligned.
- shared engine command-cycle bookkeeping (`runtime/engine_command_cycle_runtime.py`) now drives control/settings lifecycle status updates and exception publication.
- targeted settings-command/settings-engine regressions (`tests/test_settings_command_runtime.py`, `tests/test_settings_engine_agent.py`) and dashboard settings intent/UI-state helper regressions (`tests/test_dashboard_settings_intents.py`, `tests/test_dashboard_settings_ui_state.py`).
- targeted control/settings integration wiring regressions (`tests/test_dashboard_engine_wiring.py`) cover intent helper -> enqueue -> engine single-cycle -> shared-state mutation happy paths.
- new targeted scheduler dispatch-write status regression (`tests/test_scheduler_dispatch_write_status.py`) covers failed-write retry, readback reconciliation (match/mismatch/fallback), and dispatch status publication/formatting.
- targeted repo-path helper regressions in `tests/test_runtime_paths.py` cover project-root resolution from repo/test and `dashboard/` package anchors.
7. Dashboard control flow is now separated into `control/flows.py` with dedicated tests for safe-stop and transport switch semantics (source-switch helper removed from active dashboard flow).
8. Runtime shared-state initialization contract is centralized in `build_initial_shared_data(config)` with schema tests.
 - Shared-state contract now includes local emulator SoC seed request/result maps for dashboard->plant-agent local-start coordination.
 - Shared-state contract now also includes control command queue/lifecycle keys and `plant_observed_state_by_plant` cache for control-engine/dashboard coordination.
9. Runtime posting gate is canonicalized through `posting_runtime.policy_enabled` (settings-command driven), with effective API posting behavior also gated by `api_connection_runtime`.

## In Progress
1. Remote transport smoke coverage design (repeatable unattended checks).
2. Log retention policy definition and implementation scope.
3. Manual validation pass for new per-plant dispatch pause/resume semantics, scheduler readback reconciliation behavior, and Status-tab sent-setpoint/readback visibility on real remote plants.
4. Manual Schedule editor UX polish / layout tuning validation on different viewport widths (including forced terminal `end` row readability and no-delete first row behavior).
5. Manual validation pass for historical `Plots` tab behavior on larger data directories (including low-voltage voltage-axis padding/readability).
6. Evaluate per-plant queue architecture vs global queue after observing control queue + new settings queue usage on real workloads.

## Next
1. Add repeatable remote transport smoke checks.
2. Validate and document operator semantics for dispatch pause (`Paused` freezes last sent setpoint) in remote runs.
3. Define and implement log retention/cleanup policy.
4. Add lightweight dashboard visual regression/smoke checklist.
5. Expand README operator runbook/troubleshooting sections (including control engine/settings engine semantics and the new dispatch toggle behavior).
6. Decide whether to provide an optional offline recompression utility for historical dense CSV files.
7. Continue lock-discipline cleanup in `measurement/agent.py` beyond the recently refactored aggregate/current-cache/flush paths (only if contention justifies it).
8. Evaluate command cancellation/prioritization needs for long safe-stop/transport flows (if operator usage demands it).

## Known Issues / Gaps
1. No persistent store for API posting retry queue across process restarts.
2. Control-engine queue is serial and bounded; long-running stop/transport commands can delay later commands. UI now surfaces backlog/failure counts, and settings commands use a separate queue, but there is no dedicated alert/escalation behavior yet.
3. Dispatch pause (`Sending` -> `Paused`) intentionally freezes the last plant setpoint by design; no automatic zeroing occurs on pause.
4. Scheduler readback hints in the Status dispatch line are attempt-based (shown from latest scheduler write attempt) and are not refreshed on scheduler no-op match cycles.
5. Manual schedule editor drafts are stored in shared runtime state (`manual_schedule_draft_series_df_by_key`), so concurrent dashboard sessions can conflict (single-operator assumption currently accepted; per-session isolation deferred).
6. Operational runbook and incident handling guidance are still thin.
7. UI styling changes are still validated manually; no screenshot/DOM snapshot checks in CI.
8. Measurement-agent lock discipline improved in aggregate/current-cache/flush paths, but broader audit remains for lower-priority paths.
9. Historical measurement files captured while compression was inactive remain dense by design (no automatic backfill).
10. Historical `Plots` tab rescans/reads CSVs on demand and may need indexing/caching if `data/` grows large.

## Current Project Phase
Runtime architecture is stable for dual-plant operation; current priority is reliability hardening of remaining high-risk paths and operational docs.
