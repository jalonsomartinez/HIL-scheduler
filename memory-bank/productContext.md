# Product Context: HIL Scheduler

## Why This Exists
Operators need a safe, observable way to execute power schedules against real or emulated plants. The project bridges schedule ingestion, command execution, telemetry recording, and dashboard operations in one runtime.

## Primary Users
1. Operators using the private dashboard for control actions.
2. Engineers validating dispatch behavior and telemetry quality.
3. External observers using the public read-only dashboard.

## Core User Outcomes
1. Start/stop plants safely with explicit confirmation for high-impact actions.
2. Enable/pause dispatch sending independently from plant `Run/Stop`.
3. Start/stop recording per plant and review data history.
4. Switch local/remote transport safely.
5. Understand API connection, schedule readiness, control queue state, and latest measurements quickly.

## Product Behavior
- Status controls are segmented toggles with stateful labels.
- Operator dashboard supports:
  - transport toggle,
  - fleet actions (`Start All`, `Stop All`),
  - per-plant `Run/Stop`, `Dispatch/Pause`, `Record/Stopped`,
  - live and historical plots,
  - top-card summary table with per-plant latest metrics.
- Public dashboard is read-only and now includes:
  - API indicators (`API connection`, `Today's Schedule`, `Tomorrow's Schedule`) with light + background status coloring,
  - transport/error text,
  - same per-plant summary table style used in operator top card,
  - read-only mirrored control-state buttons,
  - live and historical plots.
- Plot UX emphasizes readability:
  - no redundant y-axis titles in plant plots,
  - compact legend names/order (`Pref`, `P POI`, `P Bat`, `SoC`, `Qref`, `Q POI`, `Q Bat`, `Voltage`),
  - setpoint lines dotted, POI strong colors, battery pale colors,
  - POI traces rendered above battery traces.

## UX Intent
- Separate concerns visually: transport/fleet control, summary status, detailed plant cards, historical tools.
- Keep action labels short and stateful (`Dispatch`, `Dispatching`, `Starting`).
- Use consistent geometry (radius/padding/alignment) across top-card elements.
- Improve scan speed with indicator lights, table alignment, and reduced visual noise.

## Critical Workflows
1. Plant start/stop: confirmation modal -> queued command -> control engine executes safe sequence -> UI reflects runtime state.
2. Dispatch toggle: user toggles send/pause -> command queue -> scheduler gate updates.
3. Fleet action: modal-confirmed start/stop for both plants.
4. Transport switch: modal-confirmed switch with safe-stop and cache invalidation.
5. Public monitoring: observer checks API indicators, top summary table, then drill-down in plots/history.
