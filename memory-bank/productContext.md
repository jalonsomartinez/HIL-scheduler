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
- Plant measurements remain plant-specific, while shared digital-twin summary metrics are recorded into dedicated daily twin-history files.
- Voltage setpoint source precedence is:
  - current manual voltage value when present,
  - otherwise digital-twin-derived voltage reference from battery voltage plus global min/max voltage summary,
  - otherwise fallback `1.0 pu`,
  - then clamp the resolved runtime value to `[0.9, 1.1]`.
- Grid Map summaries now show digital-twin battery voltage alongside min/max voltage and loading cards, and the page can switch between with-battery and no-battery scenarios without recomputing.
- Plots pages now include three shared digital-twin historical figure groups above the per-plant charts:
  - with battery,
  - no battery,
  - signed impact (`with_battery - without_battery`).
- Shared digital-twin history is authoritative from dedicated twin file families:
  - `*_twin.csv`,
  - `*_twin_nobat.csv`.

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
5. Historical review: users compare total/day-ahead/mFRR intent, `V ref`, with-battery digital-twin history, no-battery digital-twin history, signed impact between the two, and measured plant response from plant CSV-backed history.
