# Progress: HIL Scheduler

## Working Now
1. Dual logical plants (`lib`, `vrfb`) run under a shared global source/transport model with per-plant dispatch and recording gates.
2. Scheduler dispatches per plant from manual or API maps and applies API stale-setpoint guardrails.
 - Normalized plant Modbus register maps use canonical setpoint keys `p_setpoint` / `q_setpoint` (loader still accepts legacy `*_in` aliases).
3. Local emulation runs both plant Modbus servers concurrently with SoC and power-limit behavior.
 - Local emulation startup SoC is configured once via `startup.initial_soc_pu` and applied to both plants.
4. Measurement pipeline provides:
- anchored sampling timing,
- per-plant daily recording,
- tolerance-based compression for stable measurement runs with explicit session boundaries,
- configurable keep-gap retention (`recording.compression.max_kept_gap_s`) and last-kept-row comparison anchoring to prevent drift,
- in-memory plot cache,
- API measurement posting with retry/backoff, per-plant telemetry, and token re-auth retry on `401`/`403`.
 - API schedule fetcher next-day polling gate uses normalized `istentore_api.tomorrow_poll_start_time` (flattened `ISTENTORE_TOMORROW_POLL_START_TIME`) with explicit today/tomorrow fetch intent logging and partial-window warning/error visibility.
5. Dashboard provides:
- per-plant Start/Stop + Record/Stop controls,
- top-card `Start All` / `Stop All` controls with confirmation modal for high-impact actions,
- global source/transport switching with confirmation and safe-stop,
- `Status` tab (formerly `Status & Plots`) live status + control plots,
- `Plots` tab historical measurement browsing from `data/*.csv` with full-range timeline + range slider,
- per-plant historical exports (cropped CSV and client-side PNG),
- API-tab runtime posting toggle (`Enabled`/`Disabled`) for read-only tests,
- API status and posting health, including inline today/tomorrow per-plant fetch counts in Status tab,
- logs tab with live `Today` (current date file tail) and selectable historical files,
- branded UI theme (tokenized CSS, local font assets, flatter visual treatment, minimal corner radius, menu-style tab strip, full-width tab content cards, white page background).
6. Automated validation now includes:
- module compile checks (`python3 -m py_compile *.py`),
- unit/smoke regression suite (`python -m unittest discover -s tests -v`),
- CI execution via `.github/workflows/ci.yml`.
 - targeted historical-plots helper unit tests in `tests/test_dashboard_history.py` (environment-dependent on local pandas install).
 - targeted measurement compression regressions covering keep-gap retention and last-kept-row drift prevention.
 - targeted config-loader regressions covering shared startup SoC parsing and legacy alias mapping.
 - targeted config-loader regression coverage for `tomorrow_poll_start_time` normalization and legacy `poll_start_time` rejection.
 - targeted data fetcher regression coverage for next-day gate timing, partial/complete tomorrow fetch status, and rollover promotion (environment-dependent on local pandas install).
7. Dashboard control flow is now separated into `dashboard_control.py` with dedicated tests for safe-stop and global switch semantics.
8. Runtime shared-state initialization contract is centralized in `build_initial_shared_data(config)` with schema tests.
9. Runtime posting gate now includes `measurement_posting_enabled` state seeded from config and adjustable from dashboard UI.

## In Progress
1. Follow-up reliability design for dashboard callback de-blocking (replace synchronous Modbus reads with cached plant state).
2. Remote transport smoke coverage design (repeatable unattended checks).
3. Log retention policy definition and implementation scope.
4. Manual validation pass for new historical `Plots` tab behavior on larger data directories.

## Next
1. Add repeatable remote transport smoke checks.
2. Define and implement log retention/cleanup policy.
3. Add lightweight dashboard visual regression/smoke checklist.
4. Expand README operator runbook/troubleshooting sections.
5. Decide whether to provide an optional offline recompression utility for historical dense CSV files.

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
