# Active Context: HIL Scheduler

## Current Focus (Now)
1. Keep memory bank aligned with the dual-plant runtime contract and avoid stale schema drift.
2. Maintain robust plant control safety (start/stop transitions and guarded global switches).
3. Improve confidence through targeted automated tests around scheduler gating, recording boundaries, and API posting retry behavior.

## Open Decisions and Risks
1. Test coverage gap: no focused regression suite yet for core control contracts.
2. API posting durability: retry queue is in-memory and is lost on restart.
3. Logging retention policy: current file output is per-day file naming without automatic historical pruning.
4. Operational validation gap: limited scripted end-to-end verification for remote transport behavior.

## Rolling Change Log (Compressed, 30-Day Window)

### 2026-02-20
- Updated Istentore API auth-retry policy to treat `403` like `401` for token renewal.
- Bounded schedule fetch auth recovery to a single re-auth retry to avoid unbounded recursion.
- Kept measurement post auth recovery as one retry, now triggered by either `401` or `403`.

### 2026-02-19
- Completed dual-plant runtime consolidation across config, scheduler, measurement, dashboard, and fetcher.
- Restored dashboard safety/observability features after migration:
  - logs tab,
  - source switch confirmation and safe-stop flow,
  - per-plant transition-aware controls,
  - stable plot `uirevision` behavior.
- Added measurement posting observability state and API-tab summaries per plant.
- Hardened posting payload conversion/validation and queue attribution metadata.

### 2026-02-18
- Added deterministic today/tomorrow rollover reconciliation in fetcher status.
- Added stale API setpoint cutoff in scheduler and immediate-start setpoint selection path.
- Introduced anchored monotonic measurement step scheduling to remove trigger drift.
- Added independent API measurement post cadence with bounded retry queue and backoff.

### 2026-02-17
- Standardized timezone-aware schedule and measurement handling across agents.
- Refactored recording to daily per-plant files with timestamp-based row routing and cache-backed plots.
- Decoupled dispatch control from recording control while preserving safe-stop semantics.

### 2026-02-02
- Improved dashboard state transitions with clearer intermediate states and faster refresh cadence.
- Added dashboard logs experience for live session stream and historical file viewing.
- Resolved initialization behavior for startup selector state loading.

### 2026-02-01
- Refactored schedule architecture to separate manual and API schedule maps.
- Simplified data fetcher polling/backoff behavior.
- Reduced lock contention in scheduler/measurement/dashboard paths for responsiveness.
