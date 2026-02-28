# Progress: HIL Scheduler

## Working Now
- Memory-bank right-sizing and reconciliation with current codebase.
- Final dashboard UX pass for top-card controls, indicators, and summary tables.

## In Progress
1. Manual end-to-end validation of dashboard behavior across desktop and mobile widths.
2. Collecting operator feedback on top-card status density vs readability.
3. Deciding next regression priority: visual snapshots vs deeper callback-level UI tests.

## Next
1. Add one lightweight visual regression check for key dashboard states.
2. Expand callback-level tests around summary table rendering and indicator class transitions.
3. Evaluate historical plot indexing/caching if data volume increases.
4. Expand runbook docs for control queue behavior and transport-switch expectations.

## Known Issues / Gaps
1. No persistent durability for measurement-post retry queue across process restarts.
2. Serialized command execution can delay subsequent commands during long safe-stop/transport sequences.
3. Manual schedule drafts are shared in server state (single-operator assumption).
4. UI visual regressions are currently caught mainly by manual review.

## Current Project Phase
Stabilization and operator-facing UX refinement on top of a mostly stable runtime architecture.
