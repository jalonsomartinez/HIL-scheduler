# Progress: HIL Scheduler

## Working Now
1. Dual logical plants (`lib`, `vrfb`) run under a shared transport model with merged schedule dispatch (API base + manual overrides), plus per-plant dispatch and recording gates.
2. Scheduler dispatches per plant from a merged effective schedule (API base + enabled manual per-signal overrides) and applies API stale-setpoint guardrails to the API base.
 - Normalized plant Modbus endpoints now expose required connection ordering (`byte_order`, `word_order`) plus structured holding-register `points` metadata (address/format/access/unit/scale).
 - Scheduler/measurement/dashboard/local-emulation Modbus I/O uses shared codec helpers (`modbus_codec.py`) instead of ad-hoc scaling/bit conversions.
 - API schedule runtime maps are now pruned to local `current day + next day` retention in the fetcher to prevent unbounded growth across day rollovers.
 - Manual override runtime series are stored as four independent series (`lib_p`, `lib_q`, `vrfb_p`, `vrfb_q`) with per-series merge toggles and are pruned to the same local two-day window.
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
- `Status` tab (formerly `Status & Plots`) live status + control plots,
- `Plots` tab historical measurement browsing from `data/*.csv` with full-range timeline + range slider,
- `Plots` tab range slider now defaults to the full detected history span when the current value is a stale/placeholder out-of-domain range (avoids first-load collapsed selection),
- compacted `Plots` tab availability timeline (`LIB`/`VRFB`) to reduce vertical whitespace while preserving the top-range overview,
- per-plant historical exports (cropped CSV and client-side PNG),
- API-tab runtime posting toggle (`Enabled`/`Disabled`) for read-only tests,
- Manual Schedule tab redesign with four always-visible manual override plots and a compact breakpoint editor for one selected series at a time,
- per-series manual override active/inactive toggles controlling merged dispatch participation,
- relative-row manual override CSV save/load (`hours, minutes, seconds, setpoint`) with first-row `00:00:00` validation,
- API status and posting health, including inline today/tomorrow per-plant fetch counts in Status tab,
- Status-tab plots intentionally show only local current-day + next-day schedule/measurement data (immediate context) and now render the merged effective schedule; historical inspection stays on `Plots`,
- logs tab with live `Today` (current date file tail) and selectable historical files,
- branded UI theme (tokenized CSS, local font assets, flatter visual treatment, minimal corner radius, menu-style tab strip, full-width tab content cards, white page background).
6. Automated validation now includes:
- module compile checks (`python3 -m py_compile *.py`),
- unit/smoke regression suite (`python -m unittest discover -s tests -v`),
- CI execution via `.github/workflows/ci.yml`.
 - targeted historical-plots helper unit tests in `tests/test_dashboard_history.py` (environment-dependent on local pandas install).
 - `tests/test_dashboard_history.py` now explicitly covers stale-placeholder and fully out-of-domain slider-range defaulting semantics.
 - targeted measurement compression regressions covering keep-gap retention and last-kept-row drift prevention.
 - targeted config-loader regressions covering shared startup SoC parsing and legacy alias mapping.
 - targeted config-loader regression coverage for `tomorrow_poll_start_time` normalization and legacy `poll_start_time` rejection.
 - targeted data fetcher regression coverage for next-day gate timing, partial/complete tomorrow fetch status, and rollover promotion (environment-dependent on local pandas install).
 - targeted data fetcher regression coverage for API schedule pruning/retention and bounded tomorrow merges.
 - targeted plot-helper regression coverage for status-window x-range cropping in `tests/test_dashboard_plotting.py` (environment-dependent on local pandas install).
 - compression-gap config-loader regression now validates schema/typing (not a fixed `max_kept_gap_s` value) so config tuning does not cause CI failures.
- targeted measurement-storage SoC lookup regressions (`tests/test_measurement_storage_latest_soc.py`) and plant-agent local SoC seed request regressions (`tests/test_plant_agent_soc_seed_requests.py`).
 - targeted scheduler merged-dispatch regressions (`tests/test_scheduler_source_switch.py`) covering manual override priority and stale API base fallback behavior.
 - targeted posting telemetry regression coverage confirming posting gate no longer depends on `active_schedule_source`.
7. Dashboard control flow is now separated into `dashboard_control.py` with dedicated tests for safe-stop and transport switch semantics (source-switch helper removed from active dashboard flow).
8. Runtime shared-state initialization contract is centralized in `build_initial_shared_data(config)` with schema tests.
 - Shared-state contract now includes local emulator SoC seed request/result maps for dashboard->plant-agent local-start coordination.
9. Runtime posting gate now includes `measurement_posting_enabled` state seeded from config and adjustable from dashboard UI.

## In Progress
1. Follow-up reliability design for dashboard callback de-blocking (replace synchronous Modbus reads with cached plant state).
2. Remote transport smoke coverage design (repeatable unattended checks).
3. Log retention policy definition and implementation scope.
4. Manual validation pass for new historical `Plots` tab behavior on larger data directories.
5. Manual Schedule editor UX polish / layout tuning validation on different viewport widths.

## Next
1. Add repeatable remote transport smoke checks.
2. Define and implement log retention/cleanup policy.
3. Add lightweight dashboard visual regression/smoke checklist.
4. Expand README operator runbook/troubleshooting sections.
5. Decide whether to provide an optional offline recompression utility for historical dense CSV files.
6. Consider removing deprecated compatibility-only `active_schedule_source` / `schedule_switching` shared-state keys after downstream checks.

## Known Issues / Gaps
1. No persistent store for API posting retry queue across process restarts.
2. Dashboard status callback still performs direct Modbus polling and can block under slow endpoints.
3. Operational runbook and incident handling guidance are still thin.
4. UI styling changes are still validated manually; no screenshot/DOM snapshot checks in CI.
5. `schedule_manager.py` remains in repository for legacy compatibility only and is intentionally deprecated.
6. Historical measurement files captured while compression was inactive remain dense by design (no automatic backfill).
7. Historical `Plots` tab rescans/reads CSVs on demand and may need indexing/caching if `data/` grows large.

## Current Project Phase
Runtime architecture is stable for dual-plant operation; current priority is reliability hardening of remaining high-risk paths and operational docs.
