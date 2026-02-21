# Roadmap: HIL Scheduler

## Goal
Increase reliability and operator confidence without changing the core dual-plant runtime model, while keeping dashboard usability stable.

## Priority Order

### P0 - Reliability and Regression Safety
1. Add tests for scheduler dispatch gating and API stale cutoff behavior.
2. Add tests for recording boundaries, per-day routing, and stop flush semantics.
3. Add tests for API posting queue retry/backoff/overflow and telemetry state updates.
4. Add tests for API auth retry semantics (`401`/`403` re-auth once, then fail fast on repeated auth errors).
5. Add a scripted smoke test covering local mode start/stop/record/switch flows.

### P1 - Operational Hardening
1. Define log retention policy and implement cleanup automation.
2. Add structured health checks for API connectivity and posting backlog age.
3. Add explicit operator alerts for sustained posting failures or stale schedule windows.
4. Add an operator UI validation checklist for critical control/readability states after styling updates, including logs-tab `Today` live refresh and historical-file selection behavior.

### P2 - Developer Experience
1. Add CI checks for syntax, tests, and basic static quality gates.
2. Expand README with architecture diagram, control semantics, and troubleshooting.
3. Document recommended local test workflow for dual-plant scenarios.
4. Define a low-overhead visual regression guardrail (for example, deterministic screenshots of key dashboard tabs).

### P3 - Product Enhancements
1. Improve manual schedule validation and preview diagnostics.
2. Add richer historical analysis views for recorded sessions.
3. Evaluate persistence options for measurement posting queue durability.

## Exit Criteria for Current Phase
1. Core control and recording contracts are covered by automated tests.
2. Local smoke workflow is repeatable and documented.
3. Critical failures surface quickly via logs/status without deep code inspection.
