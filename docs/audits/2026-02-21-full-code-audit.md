# HIL Scheduler Full Code Audit

Date: 2026-02-21
Audited scope: `dashboard_agent.py`, `measurement_agent.py`, `config_loader.py`, `scheduler_agent.py`, `data_fetcher_agent.py`, `plant_agent.py`, `hil_scheduler.py`, `manual_schedule_manager.py`, `schedule_manager.py`, `logger_config.py`, `time_utils.py`, `utils.py`, `config.yaml`, `README.md`, `memory-bank/*.md`.

## 1. Dependency and Reachability Map

### Runtime entry path
- `hil_scheduler.py:35` is the runtime entrypoint.
- `hil_scheduler.py:95` starts these daemon agents: `data_fetcher_agent`, `scheduler_agent`, `plant_agent`, `measurement_agent`, `dashboard_agent`.

### Reachable runtime modules (active)
- Core orchestration: `hil_scheduler.py`.
- Runtime configuration: `config_loader.py`.
- Scheduler/dispatch: `scheduler_agent.py`.
- Plant emulation: `plant_agent.py`.
- Schedule ingestion: `data_fetcher_agent.py`, `istentore_api.py`.
- Measurement + posting: `measurement_agent.py`, `istentore_api.py`.
- Dashboard/UI control path: `dashboard_agent.py`, `manual_schedule_manager.py`.
- Time and conversions: `time_utils.py`, `utils.py`.
- Logging: `logger_config.py`.

### Orphaned / legacy-only paths
- `schedule_manager.py` is not referenced by runtime import graph.
- `schedule_manager.py` includes a standalone manager implementation and `__main__` test harness that are unreachable from production startup path.
- `config_loader.py` still exports broad compatibility aliases consumed mostly by legacy code patterns and `schedule_manager.py` compatibility helper areas.

## 2. Structural Complexity Profile

### File size concentration (LoC)
- `dashboard_agent.py` 1701
- `measurement_agent.py` 780
- `schedule_manager.py` 557
- `config_loader.py` 469

### Function concentration
- `dashboard_agent.py`: 41 function definitions, 9 Dash callbacks, many nested closures.
- `measurement_agent.py`: 33 function definitions inside a single outer agent function.
- `config_loader.py`: 12 function definitions, one very large `load_config` path.

### Lock usage concentration
- `dashboard_agent.py`: 29 `shared_data["lock"]` sections.
- `measurement_agent.py`: 17 `shared_data["lock"]` sections.
- `data_fetcher_agent.py`: 7 `shared_data["lock"]` sections.
- `scheduler_agent.py`: 1 `shared_data["lock"]` section.

### Monolith zones (extraction targets)
- `dashboard_agent.py`: mixed concerns (layout, plotting, Modbus I/O, control orchestration, logs I/O, API status rendering).
- `measurement_agent.py`: mixed concerns (sampling scheduler, recording lifecycle, file/cache sync, post queue state machine, API auth handling).
- `config_loader.py`: mixed concerns (schema normalization + legacy compatibility key fan-out).

## 3. Duplication Matrix

| Concern | Current duplicates | Recommended canonical owner |
|---|---|---|
| Modbus endpoint/register resolution | `scheduler_agent.py:get_endpoint`, `measurement_agent.py:get_transport_endpoint`, `dashboard_agent.py:get_plant_modbus_config` | Shared helper module (for example `runtime_contracts.py`) |
| Current schedule setpoint lookup + API stale cutoff | `scheduler_agent.py` main loop and `dashboard_agent.py:get_latest_schedule_setpoint` | Shared helper (for example `schedule_runtime.py`) |
| Schedule merge/append semantics | `data_fetcher_agent.py:_merge_schedule`, `manual_schedule_manager.py:append_schedules`, `schedule_manager.py:_append_to_schedule` | Shared dataframe merge helper |
| Plant filename sanitization | `measurement_agent.py:sanitize_plant_name`, `dashboard_agent.py:sanitize_name_for_filename` | Shared utility function |
| Repeated shared-data lock/read snapshots | Broad repetition in dashboard and measurement modules | Shared state accessor wrappers |

## 4. Findings (Severity-Ranked)

### Critical
No critical correctness defects were found in static review.

### High

1. Orphaned legacy scheduler implementation increases divergence risk.
- File references: `schedule_manager.py:38`, `schedule_manager.py:464`, `hil_scheduler.py:7`.
- Issue: A full alternate schedule subsystem exists but is not wired into runtime.
- Risk: Future fixes can be applied to dead path while active path diverges.
- Recommendation: Mark module explicitly deprecated, quarantine from active docs, and plan removal in Stage C if no external dependency.
- Migration risk: Low to medium (depends on external scripts relying on this file).
- Test impact: Add import/reachability test asserting active runtime modules only.

2. Dashboard callback path performs synchronous Modbus I/O repeatedly and can block UI responsiveness.
- File references: `dashboard_agent.py:171`, `dashboard_agent.py:189`, `dashboard_agent.py:1509`.
- Issue: `update_status_and_graphs` performs per-interval `read_enable_state` (network I/O) for each plant.
- Risk: Remote endpoint slowness can stall callback loop and degrade control UX.
- Recommendation: Move live Modbus polling into an agent-side cached status publisher; callbacks should read cached state only.
- Migration risk: Medium.
- Test impact: Add timeout/failure simulation and callback latency assertions.

3. Setpoint/staleness logic duplicated across scheduler and dashboard start path.
- File references: `scheduler_agent.py:107`, `dashboard_agent.py:259`.
- Issue: Two implementations of as-of lookup and API stale handling.
- Risk: Behavioral drift under future edits (start-path setpoint differs from periodic dispatch).
- Recommendation: Extract one shared function with contract tests.
- Migration risk: Medium.
- Test impact: Table-driven tests for empty, fresh, stale, and NaN schedule rows.

### Medium

4. Shared-data lock contract is inconsistently applied for dataframe operations.
- File references: `systemPatterns.md` lock discipline vs `measurement_agent.py:222`, `measurement_agent.py:231`, `measurement_agent.py:275`.
- Issue: `pd.concat`/dataframe mutation occurs inside lock in some paths.
- Risk: Lock hold time inflation and callback/agent contention under load.
- Recommendation: Copy references under lock, compute outside lock, write back under lock.
- Migration risk: Medium.
- Test impact: Concurrency smoke with high-frequency measurement and dashboard refresh.

5. Configuration surface includes keys that are parsed but inactive in active runtime behavior.
- File references: `config_loader.py:346`, `config_loader.py:377`, `measurement_agent.py:51`, `dashboard_agent.py:24`.
- Issue: `schedule.*` and `recording.compression.*` are loaded but not used by active dispatch/measurement flow.
- Risk: Operator confusion and false expectation that tuning these keys affects runtime.
- Recommendation: Document keys as legacy/inactive or wire them into runtime; for this phase prefer explicit docs + deprecation note.
- Migration risk: Low.
- Test impact: Add config-contract test that flags parsed-but-unused keys.

6. Dashboard module has broad concern overlap and high internal coupling.
- File references: `dashboard_agent.py:24`, `dashboard_agent.py:440`, `dashboard_agent.py:919`.
- Issue: UI composition, plotting, Modbus control, file-log browsing, and API status formatting coexist in one module.
- Risk: Regression probability increases on any UI/control change.
- Recommendation: Split into layout, callbacks-control, callbacks-observability, plotting helpers, and modbus-control helpers.
- Migration risk: Medium.
- Test impact: Callback unit tests by responsibility slice.

7. Data fetcher relies on private API client field.
- File reference: `data_fetcher_agent.py:118`.
- Issue: Reads `api._password` directly.
- Risk: Breaks if API client internals change.
- Recommendation: Add public `password_matches`/`set_password_if_changed` method.
- Migration risk: Low.
- Test impact: API client unit tests for password update flow.

### Low

8. Unused helper remains in dashboard logs path.
- File reference: `dashboard_agent.py:370`.
- Issue: `format_log_entries` is not used by current logs rendering flow.
- Risk: Minor maintenance noise.
- Recommendation: Remove or rewire intentionally.
- Migration risk: Low.
- Test impact: None.

9. `utils.py` contains conversion helpers not referenced in active runtime.
- File reference: `utils.py:12`.
- Issue: `kwh_to_hwh` and `hwh_to_kwh` are currently unused.
- Risk: Low (noise).
- Recommendation: Keep only if part of planned public utility surface; otherwise prune.
- Migration risk: Low.
- Test impact: None.

## 5. Staged Refactor Roadmap (Moderate Cleanup)

### Stage A: Shared pure utility extraction (no behavior change)
- Extract shared Modbus endpoint/register resolver.
- Extract shared schedule-asof + API-stale decision helper.
- Extract shared schedule merge helper and filename sanitizer.
- Add thin shared-data snapshot/update helpers.
- Acceptance criteria:
  - No dashboard UX/controls changed.
  - Scheduler and dashboard use same setpoint/stale helper.
  - `py_compile` passes.

### Stage B: Concern separation in runtime modules
- Split `dashboard_agent.py` into modules by concern:
  - layout composition,
  - control callbacks,
  - observability/log callbacks,
  - plotting/theme.
- Split `measurement_agent.py` into components:
  - sampling transport,
  - recording state machine,
  - post queue manager,
  - cache/file sync.
- Acceptance criteria:
  - Existing callback IDs and UI behavior preserved.
  - Measurement file semantics unchanged (boundaries/rollover).

### Stage C: Retire low-risk legacy paths
- Mark `schedule_manager.py` as deprecated and remove from active docs/contracts.
- Reduce compatibility alias surface in `config_loader.py` where only dead paths consume it.
- Acceptance criteria:
  - Runtime startup path unchanged.
  - README and memory-bank accurately reflect active path.

### Stage D: Contracts/tests/docs hardening
- Add targeted tests for:
  - dispatch gating and stale cutoff,
  - recording boundaries + midnight rollover,
  - post queue retry/overflow telemetry,
  - source/transport safe-switch flows.
- Add static checks in CI and contract assertions around shared-state schema.
- Acceptance criteria:
  - Critical runtime contracts covered by automated tests.
  - Active memory-bank docs aligned with code reality.

## 6. Validation Run

### Static validation executed
- Command: `python3 -m py_compile *.py`
- Result: success (no syntax/import-time compile errors).

### Lint/type checks
- No configured lint/type command was found in repository metadata during this pass.

## 7. Proposed Next Implementation PR Split

1. PR-1: Stage A utility extraction + tests for helper parity.
2. PR-2: Dashboard concern split preserving callback interface.
3. PR-3: Measurement concern split + queue/recording regression tests.
4. PR-4: Legacy path deprecation cleanup + docs/memory reconciliation.

## 8. Implementation Status (Updated 2026-02-21)

### Completed Stages
- Stage A completed (`273d958`): shared contracts/helpers extracted.
- Stage B completed (`d674ae7`): dashboard + measurement concern split.
- Regression fixes completed (`cb207b6`): measurement record-start NameError and logs parsing regex.
- Stage C completed (`c980593`): `schedule_manager.py` deprecated; legacy config alias surface gated behind env flag.
- Stage D (tests + validation) expanded:
  - `3c03aa5`, `5bfbca3`, `bc4fa5a`: regression and smoke tests for logs, recording, scheduler stale-switch behavior, and posting telemetry.
  - `9acbf74`: CI compile + unittest workflow added.

### Remaining High-Priority Follow-Ups
1. Safe-stop and source/transport switch callback regression tests.
2. Remote transport smoke workflow equivalent to current local smoke coverage.
3. Dashboard callback de-blocking (replace synchronous Modbus polling with cached agent state).
