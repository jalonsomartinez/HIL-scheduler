# Roadmap: HIL Scheduler

## Goal
Increase operational confidence by stabilizing multi-market schedule composition and observability, while preserving safe dual-plant control behavior.

## Priority Order
1. Reliability guardrails
- Keep compile/unit checks green for fetcher/scheduler/measurement/dashboard paths.
- Preserve strict config/schema validation for schedule/polling keys.
- Validate `total = day-ahead + mFRR` behavior under real API payload variability.
- Preserve local SoC continuity and safe-stop behavior while schedule stack evolves.

2. Operational hardening
- Confirm runtime mFRR polling cadence/telemetry accuracy in production-like runs.
- Keep mFRR logging high-signal (transition INFO, steady-state DEBUG, failure ERROR).
- Continue remote endpoint stability checks after pooled-session rollout.
- Decide scheduler readback grouping scope (grouped vs point-wise).

3. UX and observability
- Add lightweight visual regression checks for schedule traces (`Pref`, `day-ahead`, `mfrr`).
- Refine API tab messaging around polling windows/cadence if operator feedback requests it.
- Keep private/public status summaries aligned and unambiguous.

4. Scalability and maintainability
- Evaluate history indexing/caching for large `data/` directories.
- Continue low-risk dedup of shared schedule/runtime helpers.
- Revisit per-session manual draft isolation if multi-operator workflows are needed.
- Revisit credential storage/security model if threat model expands.

## Exit Criteria for Current Phase
1. Total/day-ahead/mFRR schedules are correct and stable in live and historical views.
2. mFRR telemetry in API tab accurately reflects runtime state (`last`, `result`, `next`, errors).
3. Measurement CSV/history consistently includes new schedule-intent columns without breaking legacy loads.
4. mFRR poll logs remain low-noise while preserving actionable operational signals.
5. Core control/safe-stop/transport behavior remains stable in local and remote operation.
