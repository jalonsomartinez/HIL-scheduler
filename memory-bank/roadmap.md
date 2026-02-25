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
6. Keep API schedule fetcher poll-gate/config regression coverage green (`tomorrow_poll_start_time` parsing, gate timing, partial-window status semantics).
7. Add dashboard/manual-editor callback regressions for key manual override UX flows (series selector load, CSV upload, breakpoint add/delete, row validation).
8. Add one regression covering control-engine command queue overflow visibility/behavior (`queue_full` rejection path surfaced to UI/log state).
9. Add one UI-level regression (or helper-level regression if callback tests remain brittle) for Status-tab control-engine/queue/Modbus health text rendering.
10. Add one integration regression covering settings-command wiring (manual activate/update/inactivate and API/posting command enqueue -> settings-engine state mutation path).

### P1 - Operational Hardening
1. Define log retention policy and implement cleanup automation.
2. Validate and tune control-engine observed-state cache cadence/staleness thresholds and transition UX hold timing (`starting`/`stopping` immediate feedback vs server-confirmed state) using real-server latency.
3. Add structured health checks for API connectivity and posting backlog age.
4. Add explicit operator alerts for sustained posting failures, stale schedule windows, command-queue saturation, settings-queue saturation, or persistent Modbus read/connect failures.
5. Add an operator UI validation checklist for critical control/readability states after styling updates, including logs-tab `Today` live refresh, historical-file selection behavior, bulk-action confirmation states, and small-screen control-row behavior.
6. Validate and refine the new Manual Schedule split-layout/editor ergonomics across desktop/tablet breakpoints.

### P2 - Developer Experience
1. Expand README with architecture diagram, control semantics, and troubleshooting.
2. Document recommended local/remote smoke workflow for dual-plant scenarios.
3. Define a low-overhead visual regression guardrail (for example, deterministic screenshots of key dashboard tabs).
4. Plan final removal of deprecated legacy compatibility paths (`schedule_manager.py`, alias fallback flag) after external dependency check.
5. Decide whether to ship an optional offline utility for historical CSV recompression.
6. Evaluate queue topology evolution (global FIFO vs per-plant queues with global command barriers) after collecting operator observations from control-queue/health UI and new settings-queue behavior.

### P3 - Product Enhancements
1. Expand manual override editor validation feedback/diagnostics (without reintroducing UI clutter).
2. Expand the new historical `Plots` tab beyond baseline browsing/exports (for example: file filters, derived stats, multi-range compare).
3. Evaluate persistence options for measurement posting queue durability.

## Exit Criteria for Current Phase
1. Core control and recording contracts are covered by automated tests, including safe-stop/switch flows.
2. Local and remote smoke workflows are repeatable and documented.
3. Critical failures surface quickly via logs/status without deep code inspection.
