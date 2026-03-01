# Progress: HIL Scheduler

## Working Now
- Memory-bank reconciliation to reflect local SoC continuity changes.
- Monitoring local fleet start behavior to confirm first recorded SoC reflects seeded value.

## In Progress
1. End-to-end local-mode SoC continuity validation:
   - startup seed from persisted SoC,
   - local `Start All` ordering (`start/seed -> recording`),
   - fallback behavior to `STARTUP_INITIAL_SOC_PU` when no persisted SoC exists.
2. End-to-end validation of API credential flows:
   - env preload (`HIL_API_PASSWORD`) -> stored runtime password,
   - manual save via API tab,
   - connect/disconnect transitions and fetch/posting gates.
3. Confirming startup scripts usage on Linux/Windows with local untracked env files.
4. Reviewing whether API tab needs explicit clear-password action in current phase.

## Next
1. Add one lightweight visual regression check for key dashboard states.
2. Expand callback-level tests around API tab controls and auth/credential status messaging.
3. Evaluate indexing/caching strategy for large `data/` directories to reduce startup SoC-lookup overhead.
4. Expand runbook docs for control queue behavior, startup scripts, credential env conventions, and local SoC seed semantics.

## Known Issues / Gaps
1. No persistent durability for measurement-post retry queue across process restarts.
2. Serialized command execution can delay subsequent commands during long safe-stop/transport sequences.
3. Manual schedule drafts are shared in server state (single-operator assumption).
4. UI visual regressions are currently caught mainly by manual review.
5. API password is process-memory only (env/bootstrap or dashboard save); no encrypted persistence layer.

## Current Project Phase
Stabilization and operational hardening on top of a mostly stable runtime architecture.
