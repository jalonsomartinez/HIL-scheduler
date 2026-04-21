# Roadmap: HIL Scheduler

## Goal
Increase operational confidence by stabilizing heterogeneous Modbus dispatch, including the new voltage-regulation path, while preserving safe dual-plant control behavior and clear observability.

## Priority Order
1. Reliability guardrails
- Keep compile and targeted unit checks green for scheduler, control, measurement, dashboard, and config paths.
- Preserve strict config validation for aggregate/per-phase setpoints and `q_control_mode` + `v_setpoint` requirements.
- Validate stale-API behavior with per-signal manual overrides, including manual voltage mode selection.

2. Operational hardening
- Confirm direct plant voltage-control behavior on real LIB endpoints with `q_control_mode` + `v_setpoint`.
- Confirm the deadbanded digital-twin-derived voltage reference behaves sensibly under realistic battery-voltage and low-voltage combinations around the `0.925 pu` threshold.
- Decide whether trigger pulse timing should remain fixed or become configurable.
- Continue remote endpoint stability checks after pooled-session rollout.
- Extend non-dispatch runtime paths if per-phase-only telemetry becomes a production requirement.

3. UX and observability
- Keep private/public status summaries aligned around `V ref` and measured voltage.
- Keep Grid Map summary cards and scenario toggle aligned with the dual-scenario digital-twin summary contract.
- Keep dedicated `twin` / `twin_nobat` history files and shared historical plots aligned with the same summary contract, including voltage-bucket node counts and signed comparison behavior.
- Add any lightweight follow-up coverage needed for six-channel manual schedule UI behavior.
- Refine operator messaging if voltage-mode field validation shows confusion around activation semantics.

4. Scalability and maintainability
- Evaluate history indexing/caching for large `data/` directories.
- Continue low-risk dedup of shared dispatch/runtime helpers.
- Revisit per-session manual draft isolation if multi-operator workflows are needed.

## Exit Criteria for Current Phase
1. Aggregate, per-phase, trigger-latched, and voltage-regulation endpoints all dispatch correctly with clear failure reporting.
2. Manual voltage override cleanly selects reactive mode and produces stable `Q` behavior in field validation.
3. `v_setpoint_pu` remains correct and visible in live status and recorded telemetry, whether sourced from manual override or the digital twin.
4. Digital-twin summary metrics and voltage-bucket node counts remain consistent across live Grid Map scenarios, twin-history files, historical plots, and Grid Map summary surfaces.
5. SoC remains usable through direct reads or estimator fallback without breaking recording/history flows.
6. Core control, safe-stop, and transport behavior remains stable in local and remote operation.
