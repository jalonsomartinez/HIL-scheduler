# Progress: HIL Scheduler

## Working Now
- Validating per-phase and trigger-latched Modbus dispatch behavior against the live scheduler/control contracts.
- Confirming trigger-aware apply latency and retry behavior on realistic plant timings.
- Verifying SoC estimation fallback remains stable when real Modbus `soc` is absent.
- Verifying mFRR + day-ahead composition behavior in runtime and dashboards.
- Stabilizing menu-only private/public navigation after the tab removal.
- Preserving the current grid-map digital-twin audit trail for later technical review.

## In Progress
1. Dispatch-path hardening
   - aggregate vs per-phase setpoint schema support
   - exact-register readback suppression of redundant writes
   - trigger-aware apply sequencing
2. SoC continuity hardening
   - optional Modbus `soc` schema support
   - shared startup seed resolution
   - measurement-time estimation fallback
3. Schedule-model rollout
   - dual fetch cadence (day-ahead + mFRR)
   - total schedule recomposition and retention pruning
4. Observability rollout
   - mFRR poll telemetry in the private API page
   - reduced-noise mFRR logs
   - richer scheduler dispatch status context
5. Dashboard/navigation stabilization
   - menu-driven route sections
   - preserve existing control and plotting IDs
6. Grid-map audit follow-up
   - keep mirrored digital-twin pickle copies in sync
   - preserve backups and audit notes for temporary transformer-header edits

## Next
1. Run dependency-enabled tests for the April 13 dispatch/SoC changes in an environment with `pytest`, `pandas`, and `dash`.
2. Field-validate per-phase setpoint writes and trigger-latched apply behavior on target hardware.
3. Decide whether measurement and local-emulator paths need full per-phase telemetry support.
4. Perform field validation for mFRR polling windows and transition-based logging behavior.
5. Continue remote Modbus reliability hardening and pooled-client smoke-test adaptation.
6. Confirm with the technical team whether transformer-header lines `841-848` should keep the current investigative geometry-based values.

## Known Issues / Gaps
1. No durable persistence for the measurement-post retry queue across process restarts.
2. Serialized command execution can delay later commands during long safe-stop/transport sequences.
3. Manual schedule drafts are shared in server state (single-operator assumption).
4. UI visual regressions are still caught mainly by manual review.
5. API password is process-memory only; there is no encrypted persistence layer.
6. `tests.test_local_runtime_smoke` still fails under pooled Modbus fake-client patching.
7. Current local shell may lack dependencies for full test execution (`pytest`, `pandas`, `dash`).
8. Trigger-latched setpoint apply currently blocks for about two seconds per successful apply with default helper timing.
9. Per-phase setpoint support is complete in scheduler/control write paths, but measurement sampling and local emulator loops still rely on aggregate setpoint assumptions.
10. The current grid-map pandapower pickle contains investigative edits for transformer-header lines `841-848` that are documented but not yet validated as final network parameters.

## Current Project Phase
Heterogeneous Modbus dispatch hardening and SoC continuity support, alongside schedule observability work and grid-map digital-twin audit follow-up.
