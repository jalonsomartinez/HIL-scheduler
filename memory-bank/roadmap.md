# Roadmap: HIL Scheduler

## Goal
Increase operational confidence by stabilizing heterogeneous Modbus dispatch, multi-market schedule composition, and observability while preserving safe dual-plant control behavior.

## Priority Order
1. Reliability guardrails
- Keep compile/unit checks green for fetcher, scheduler, measurement, control, and dashboard paths.
- Preserve strict config/schema validation for aggregate-vs-per-phase setpoint endpoints.
- Validate SoC fallback behavior when hardware omits a direct `soc` register.
- Validate `total = day-ahead + mFRR` behavior under real API payload variability.
- Preserve local SoC continuity and safe-stop behavior while the dispatch stack evolves.

2. Operational hardening
- Confirm per-phase setpoint writes and trigger-latched apply behavior on real endpoints.
- Decide whether trigger pulse timing should remain fixed or become configurable.
- Confirm runtime mFRR polling cadence/telemetry accuracy in production-like runs.
- Keep mFRR logging high-signal (transition INFO, steady-state DEBUG, failure ERROR).
- Continue remote endpoint stability checks after pooled-session rollout.
- Extend non-dispatch runtime paths if per-phase-only telemetry becomes a production requirement.
- Reconcile grid-map pandapower geometry with electrical lengths before treating current local patches as canonical.

3. UX and observability
- Add lightweight regression coverage for schedule traces (`Pref`, `day-ahead`, `mfrr`).
- Add lightweight regression coverage for menu-only route pages (private/public).
- Keep private/public status summaries aligned and unambiguous.
- Refine API-page messaging around polling cadence/windows if operator feedback requires it.

4. Scalability and maintainability
- Evaluate history indexing/caching for large `data/` directories.
- Continue low-risk dedup of shared schedule/runtime helpers.
- Revisit per-session manual draft isolation if multi-operator workflows are needed.
- Revisit credential storage/security if the threat model expands.

## Exit Criteria for Current Phase
1. Total/day-ahead/mFRR schedules are correct and stable in live and historical views.
2. Aggregate, per-phase, and trigger-latched setpoint endpoints all dispatch correctly with clear failure reporting.
3. SoC remains usable through direct reads or estimator fallback without breaking recording/history flows.
4. mFRR telemetry in the API page accurately reflects runtime state (`last`, `result`, `next`, errors).
5. mFRR poll logs remain low-noise while preserving actionable operational signals.
6. Core control, safe-stop, and transport behavior remains stable in local and remote operation.
