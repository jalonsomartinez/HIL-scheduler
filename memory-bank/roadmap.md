# Roadmap: HIL Scheduler

## Goal
Increase reliability and operator confidence without changing the core dual-plant runtime model, while keeping dashboard usability stable.

## Priority Order

### P0 - Reliability and Regression Safety
1. Add remote transport smoke coverage equivalent to local smoke checks.
2. Keep compile + unittest checks green in CI on every PR/push.
3. Add one integration test for dashboard callback->control wiring (ID stability + state mutation path), including new fleet-action and posting-toggle controls.
4. Add safe-stop timeout-path regression test (`threshold_reached=False`, disable fallback).
5. Keep measurement compression and config-loader schema regression coverage green to prevent config/runtime drift.

### P1 - Operational Hardening
1. Define log retention policy and implement cleanup automation.
2. Move dashboard synchronous Modbus polling to agent-cached plant-state publication.
3. Add structured health checks for API connectivity and posting backlog age.
4. Add explicit operator alerts for sustained posting failures or stale schedule windows.
5. Add an operator UI validation checklist for critical control/readability states after styling updates, including logs-tab `Today` live refresh, historical-file selection behavior, bulk-action confirmation states, and small-screen control-row behavior.

### P2 - Developer Experience
1. Expand README with architecture diagram, control semantics, and troubleshooting.
2. Document recommended local/remote smoke workflow for dual-plant scenarios.
3. Define a low-overhead visual regression guardrail (for example, deterministic screenshots of key dashboard tabs).
4. Plan final removal of deprecated legacy compatibility paths (`schedule_manager.py`, alias fallback flag) after external dependency check.
5. Decide whether to ship an optional offline utility for historical CSV recompression.

### P3 - Product Enhancements
1. Improve manual schedule validation and preview diagnostics.
2. Expand the new historical `Plots` tab beyond baseline browsing/exports (for example: file filters, derived stats, multi-range compare).
3. Evaluate persistence options for measurement posting queue durability.

## Exit Criteria for Current Phase
1. Core control and recording contracts are covered by automated tests, including safe-stop/switch flows.
2. Local and remote smoke workflows are repeatable and documented.
3. Critical failures surface quickly via logs/status without deep code inspection.
