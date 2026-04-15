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
  - the private dashboard `Q mode` / `V mode` toggle is the authoritative reactive-mode selector when the active endpoint exposes both `q_control_mode` and `v_setpoint`,
  - voltage mode writes a direct plant voltage target instead of a reactive-power target.
- Status summaries now show both measured voltage and voltage reference (`V ref`).
- Recorded measurements now also carry the latest digital-twin summary metrics, including battery voltage, min/max voltage, max line loading, overloaded-line count, and detailed voltage-bucket node counts.
- Voltage setpoint source precedence is:
  - current manual voltage value when present,
  - otherwise digital-twin-derived voltage reference from battery voltage plus global min/max voltage summary,
  - otherwise fallback `1.0 pu`,
  - then clamp the resolved runtime value to `[0.9, 1.1]`.
- Grid Map summaries now show digital-twin battery voltage alongside min/max voltage and loading cards.
- Plots pages now include a shared `Grid Map / Digital Twin` historical figure group above the per-plant charts so users can review those system-level metrics over the selected time range.

## UX Intent
- Keep operator-facing schedule semantics stable while exposing the new voltage-control path without creating a second UI workflow.
- Use the same draft/apply/update mental model for `P`, `Q`, and voltage overrides.
- Surface endpoint quirks and dispatch failures clearly instead of silently falling back.
- Keep public status readable and aligned with private status summaries.

## Critical Workflows
1. mFRR polling loop: fetch -> update per-plant mFRR maps -> recompute total schedule -> publish telemetry.
2. Manual override editing: operator edits a per-signal series -> applies it -> runtime activates or updates only that signal.
3. Voltage-regulation dispatch: operator selects `V mode` -> resolve `v_setpoint_pu` from manual-or-twin source -> convert it to the plant voltage register unit -> write `q_control_mode=3`, `P`, and `v_setpoint`.
4. Classic reactive dispatch: operator selects `Q mode` -> resolve `Q` setpoint -> write `q_control_mode=1` when available plus setpoints.
5. Historical review: users compare total/day-ahead/mFRR intent, `V ref`, shared digital-twin history with voltage buckets, and measured plant response from CSV-backed history.
