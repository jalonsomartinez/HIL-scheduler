# Active Context: HIL Scheduler

## Current Focus (Now)
- Final UI polish and consistency for operator and public dashboards.
- Keep runtime control contracts stable while improving readability and observability.
- Maintain regression safety for dashboard callbacks and plotting behavior.

## Open Decisions and Risks
- Whether to add visual regression testing (screenshots/DOM checks) to reduce CSS drift risk.
- Whether manual draft schedules should remain shared across sessions or move to per-session client state.
- Whether control/settings queue depth needs alerting/escalation behavior beyond inline status text.
- Performance risk for large `data/` directories in historical plot scans (possible index/cache work later).

## Rolling Change Log (Compressed, 30-Day Window)
- 2026-02-26 to 2026-02-27:
  - Stabilized command-runtime architecture with control/settings queues and engine status publication.
  - Added/expanded helper-level tests for control health, command flows, plotting, and schedule/runtime behavior.
  - Continued package split hygiene (`dashboard/`, `control/`, `settings/`, `measurement/`, `runtime/`, etc.).
- 2026-02-28 (dashboard UX iteration batch):
  - Dispatch controls relabeled to `Dispatch`, `Dispatching`, `Starting` and removed redundant `Dispatch` label in plant cards.
  - Public dashboard top card redesigned:
    - replaced verbose status text with three API indicators,
    - indicator light size increased,
    - indicator labels simplified (`API connection`, `Today's Schedule`, `Tomorrow's Schedule`),
    - indicator backgrounds now status-colored (green/red) with stronger contrast,
    - transport + error text moved to one line,
    - added per-plant summary table (`Plant`, `Status`, `Pref`, `P POI`, `Qref`, `Q POI`, `Voltage`).
  - Public title cleaned to `Spanish Demo Dashboard` (removed read-only suffix).
  - Status/plot readability updates:
    - removed per-subplot y-axis titles,
    - simplified legend names and fixed legend order,
    - setpoint traces dotted, POI traces strong, battery traces pale,
    - POI traces render above battery traces.
  - Operator dashboard top card updates:
    - responsive transport/fleet row behavior refined,
    - inserted same summary table under transport/fleet controls,
    - removed top-row separator above table,
    - added spacing between table and status text lines.
  - Shared header compaction:
    - reduced container/header padding,
    - restored previous logo size after review.
  - Radius consistency pass:
    - standardized top-card indicator/table/chip radii via `--public-top-radius: 4px`.
  - Fixed runtime NameError in public callback by ensuring `normalize_datetime_series` availability where latest-row table values are built.
