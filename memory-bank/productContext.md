# Product Context: HIL Scheduler

## Why This Exists
Operators need one runtime that can safely execute multi-market battery schedules and show both controller intent and plant response, even when plant Modbus interfaces differ across deployments.

## Primary Users
1. Operators using the private dashboard to control plants, dispatching, recording, and API state.
2. Engineers validating schedule composition, Modbus dispatch behavior, and telemetry quality.
3. External viewers consuming the public read-only dashboard.

## Core User Outcomes
1. Run and stop plants safely with explicit control feedback.
2. Trust that active dispatch reflects day-ahead + mFRR intent plus any active manual overrides.
3. Distinguish schedule intent (`Pref`, `day-ahead`, `mfrr`, manual voltage reference) from measured plant response.
4. Keep dispatch usable across aggregate, per-phase, trigger-latched, and optional voltage-regulation devices.
5. Preserve a usable SoC signal even when hardware does not expose a direct Modbus `soc` point.

## Product Behavior
- API schedule intent remains three-layer:
  - `Pref` is the total active-power setpoint used by dispatch,
  - `day-ahead` and `mfrr` remain visible as separate traces.
- Manual schedule intent is per signal:
  - `P` override per plant,
  - `Q` override per plant,
  - voltage-setpoint override per plant.
- Reactive dispatch behavior:
  - default mode is classic `Q` control using the resolved reactive-power setpoint,
  - activating the manual voltage channel for a plant switches that plant to voltage regulation when the active endpoint exposes `q_control_mode`,
  - voltage mode computes `Q` from measured `v_poi`, configured nominal POI voltage, configured droop, and plant Q limits.
- Status summaries now show both measured voltage and voltage reference (`V ref`).
- Voltage setpoint currently comes only from manual schedule and defaults to `1.0 pu` when no manual voltage value is available.

## UX Intent
- Keep operator-facing schedule semantics stable while exposing the new voltage-control path without creating a second UI workflow.
- Use the same draft/apply/update mental model for `P`, `Q`, and voltage overrides.
- Surface endpoint quirks and dispatch failures clearly instead of silently falling back.
- Keep public status readable and aligned with private status summaries.

## Critical Workflows
1. mFRR polling loop: fetch -> update per-plant mFRR maps -> recompute total schedule -> publish telemetry.
2. Manual override editing: operator edits a per-signal series -> applies it -> runtime activates or updates only that signal.
3. Voltage-regulation dispatch: active manual voltage channel -> resolve `v_setpoint_pu` -> compute `Q` from measured `v_poi` and droop -> write `q_control_mode=3` plus setpoints.
4. Classic reactive dispatch: inactive manual voltage channel -> resolve `Q` setpoint -> write `q_control_mode=1` when available plus setpoints.
5. Historical review: users compare total/day-ahead/mFRR intent, `V ref`, and measured plant response from CSV-backed history.
