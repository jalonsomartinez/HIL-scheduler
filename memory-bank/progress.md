# Progress: HIL Scheduler

## Working Now
- Verifying mFRR + day-ahead composition behavior in live runtime and dashboards.
- Confirming API tab mFRR polling telemetry reflects real poll state transitions.
- Validating that VRFB mFRR index now follows LIB response window and no longer expands to synthetic 2-day grids.

## In Progress
1. Schedule model rollout:
   - dual fetch cadence (day-ahead + mFRR),
   - total schedule recomposition and retention pruning.
2. Observability rollout:
   - mFRR poll telemetry in private API tab,
   - reduced-noise mFRR polling logs.
3. Measurement/history alignment:
   - recording of schedule intent columns,
   - historical plotting fallback compatibility.
4. Regression alignment:
   - extending fetcher/plot/storage tests for new contracts,
   - keeping backward compatibility for legacy CSV data.

## Next
1. Run full dependency-enabled test suite in target environments (`pytest`, `pandas`, `dash` installed).
2. Perform field validation for mFRR polling windows and transition-based logging behavior.
3. Continue remote Modbus reliability hardening and pooled-client smoke-test adaptation.
4. Evaluate API credential hardening options beyond process-memory storage.

## Known Issues / Gaps
1. No persistent durability for measurement-post retry queue across process restarts.
2. Serialized command execution can delay subsequent commands during long safe-stop/transport sequences.
3. Manual schedule drafts are shared in server state (single-operator assumption).
4. UI visual regressions are currently caught mainly by manual review.
5. API password is process-memory only (env/bootstrap or dashboard save); no encrypted persistence layer.
6. `tests.test_local_runtime_smoke` currently fails with pooled Modbus client semantics under fake-client patching.
7. Current local shell may lack Python deps for full test execution (`pytest`, `pandas`, `dash`).

## Current Project Phase
Schedule-model expansion and observability hardening, alongside continued remote reliability stabilization.
