# Project Brief: HIL Scheduler

## Overview
HIL Scheduler is a dual-plant control application for LIB and VRFB batteries. It ingests API market schedules, merges them with optional manual overrides, dispatches Modbus setpoints, records telemetry, and serves private/public dashboards.

## Core Goals
1. Dispatch active/reactive setpoints safely and on cadence.
2. Keep plant transitions safe (`start`, `stop`, `transport switch`) through queued control flows.
3. Maintain a consistent schedule model across LIB and VRFB.
4. Persist high-quality per-plant telemetry with compression and history export.
5. Provide high-signal operational observability for API polling/posting, control runtime, queue health, and write outcomes.

## Runtime Model
- Logical plants: `lib`, `vrfb`.
- Transport modes: `local`, `remote`.
- Modbus model: shared per-endpoint transport with serialized request execution; grouped reads for stable read sets.
- Schedule model:
  - `day-ahead` from market id 4,
  - `mFRR` from market id 3 polled independently,
  - `total` authoritative dispatch schedule where `total_p = day_ahead_p + mfrr_p` and reactive comes from day-ahead/manual logic.
- VRFB mFRR contract: zero-power mFRR frame aligned to LIB mFRR timestamps (no synthetic expansion).
- Control model: dashboard enqueues commands; control/settings engines execute and publish status.
- Dashboards:
  - private operator dashboard with route-based menu pages (`status`, `plots`, `manual-schedule`, `api-schedule`, `logs`),
  - public read-only dashboard with route-based menu pages (`status`, `plots`).

## In Scope
- Multi-thread agents: fetcher, scheduler, plant emulator, measurement, control engine, settings engine, private/public dashboards.
- API schedule fetch/post flows, posting retry queue, and runtime connection health.
- Per-plant recording to `data/YYYYMMDD_<plant>.csv` plus history loading/export.
- Plotting of total/day-ahead/mFRR schedule intent alongside measured telemetry.

## Hard Constraints
- Holding-register Modbus only.
- Required endpoint config: `byte_order`, `word_order`, structured `points`.
- Time handling must remain timezone-aware and normalized.
- Dispatch and settings operations remain queue-serialized.

## Success Criteria
1. Dispatch uses correct `total` schedule (day-ahead + mFRR + manual overrides).
2. LIB/VRFB schedule model is uniform and internally consistent.
3. API Schedule page clearly exposes polling state, including mFRR poll telemetry.
4. Measurement files and history retain backward compatibility while adding schedule intent columns.
5. Operator/public dashboards remain menu-only (no tab-strip regression) while preserving existing controls and observability.
