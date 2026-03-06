# Roadmap: HIL Scheduler

## Goal
Increase operational confidence through reliability hardening and high-signal dashboard UX, without changing the core dual-plant runtime model.

## Priority Order
1. Reliability guardrails
- Keep compile/unit checks green for dashboard/control/settings/scheduler/modbus paths.
- Validate new shared Modbus transport behavior under sustained dual-plant operation.
- Preserve local SoC continuity guarantees (startup restore + local fleet-start ordering) with targeted regressions.
- Preserve strict config/schema validation as source of runtime truth.
- Enforce pinned direct dependency parity across all serving hosts to prevent frontend bundle/version drift.

2. Operational hardening
- Confirm remote endpoint stability after pooled-session rollout (VRFB and LIB).
- Expand diagnostics comparison runs (`dashboard_like` / `app_like_parallel` / `app_like_serial`) as acceptance evidence.
- Decide scheduler readback grouping scope (apply grouped reads there or keep current point-wise logic).
- Add clearer operator alerts for sustained queue backlog, stale data, and repeated control-path errors.

3. UX and observability
- Add lightweight visual regression checks for operator/public status views.
- Keep private dashboard command/state feedback aligned with actual command registers in plant.
- Refine public dashboard summary density and API tab credential messaging based on operator feedback.
- Add regression checks for summary-table schema/units (`SoC` as `%`, `P ref`, `Q ref`, column order parity across dashboards).

4. Scalability and maintainability
- Evaluate history indexing/caching strategy for large `data/` folders (including startup SoC restore lookups).
- Continue low-risk dedup of shared defaults/helpers where it reduces drift.
- Keep grouped-read planning static and transparent (build at endpoint/setup time, not per-read dynamic optimization).
- Revisit per-session manual draft isolation if multi-operator use becomes required.

## Exit Criteria for Current Phase
1. Core control and dashboard callbacks remain stable under automated regression.
2. Transport and safe-stop behavior are validated in local and remote scenarios with pooled Modbus sessions.
3. Local SoC continuity is deterministic across cold startup and local `Start All`.
4. Operators can assess plant/API state quickly from top-card indicators, summary tables, and command-state feedback.
5. API credential flows are deterministic across env bootstrap and dashboard interactions.
6. Documentation (memory bank + runbook) matches runtime behavior with minimal drift and diagnostics evidence.
7. Local and remote/Tailscale-served dashboards load matching Dash component-suite versions and render controls consistently.
