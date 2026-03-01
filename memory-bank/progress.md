# Progress: HIL Scheduler

## Working Now
- Memory-bank reconciliation to reflect latest dashboard summary-table behavior.
- Keeping operator/public summary-table column schema and units synchronized.

## In Progress
1. End-to-end validation of API credential flows:
   - env preload (`HIL_API_PASSWORD`) -> stored runtime password,
   - manual save via API tab,
   - connect/disconnect transitions and fetch/posting gates.
2. Confirming startup scripts usage on Linux/Windows with local untracked env files.
3. Reviewing whether API tab needs explicit clear-password action in current phase.
4. Expanding UI checks to catch summary-table label/unit drift (`SoC %`, `P ref`, `Q ref`).

## Next
1. Add one lightweight visual regression check for key dashboard states.
2. Expand callback-level tests around API tab controls and auth/credential status messaging.
3. Evaluate historical plot indexing/caching if data volume increases.
4. Expand runbook docs for control queue behavior, startup scripts, and credential env conventions.

## Known Issues / Gaps
1. No persistent durability for measurement-post retry queue across process restarts.
2. Serialized command execution can delay subsequent commands during long safe-stop/transport sequences.
3. Manual schedule drafts are shared in server state (single-operator assumption).
4. UI visual regressions are currently caught mainly by manual review.
5. API password is process-memory only (env/bootstrap or dashboard save); no encrypted persistence layer.

## Current Project Phase
Stabilization and operational hardening on top of a mostly stable runtime architecture.
