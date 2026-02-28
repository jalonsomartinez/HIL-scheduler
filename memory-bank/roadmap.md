# Roadmap: HIL Scheduler

## Goal
Increase operational confidence through reliability hardening and high-signal dashboard UX, without changing the core dual-plant runtime model.

## Priority Order
1. Reliability guardrails
- Keep compile/unit checks green for dashboard/control/settings/scheduler paths.
- Add missing regressions around API credential flows (`api.password.set`, env preload, connect/disconnect) and UI state rendering.
- Preserve strict config/schema validation as source of runtime truth.

2. Operational hardening
- Define log retention/cleanup policy.
- Validate remote-transport behavior under intermittent connectivity.
- Add clearer operator alerts for sustained queue backlog, stale data, and repeated control-path errors.
- Decide whether to add secure credential storage and explicit password clear UX for API tab.

3. UX and observability
- Add lightweight visual regression checks for operator/public status views.
- Continue readability tuning only where it improves scan speed (not cosmetic churn).
- Refine public dashboard summary density and API tab credential messaging based on operator feedback.

4. Scalability and maintainability
- Evaluate history indexing/caching strategy for large `data/` folders.
- Continue low-risk dedup of shared defaults/helpers where it reduces drift.
- Revisit per-session manual draft isolation if multi-operator use becomes required.

## Exit Criteria for Current Phase
1. Core control and dashboard callbacks remain stable under automated regression.
2. Transport and safe-stop behavior are validated in local and remote scenarios.
3. Operators can assess plant/API state quickly from top-card indicators and summary tables.
4. API credential flows are deterministic across env bootstrap and dashboard interactions.
5. Documentation (memory bank + runbook) matches runtime behavior with minimal drift.
