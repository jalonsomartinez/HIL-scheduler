# Project Brief: HIL Scheduler

## Overview
HIL Scheduler is a dual-plant control runtime for LIB and VRFB batteries. It ingests API market schedules, merges optional manual overrides, dispatches Modbus setpoints, records telemetry, and serves private/public dashboards for operators and observers.

## Core Goals
1. Dispatch active/reactive setpoints safely and on cadence across heterogeneous Modbus endpoints.
2. Keep plant transitions safe (`start`, `stop`, `transport switch`) through queue-serialized control flows.
3. Maintain one consistent schedule model across LIB and VRFB, with `total = day-ahead + mFRR`.
4. Preserve high-quality telemetry, history export, and usable SoC even when hardware omits a direct SoC register.
5. Provide high-signal observability for API polling/posting, control runtime, queue health, and dispatch outcomes.

## Runtime Model
- Logical plants: `lib`, `vrfb`.
- Transport modes: `local`, `remote`.
- Modbus model: shared per-endpoint transport with serialized execution and grouped reads for stable read sets.
- Schedule model:
  - `day-ahead` from market id 4,
  - `mFRR` from market id 3 on its own polling cadence,
  - `total` authoritative dispatch schedule, with reactive power from day-ahead/manual logic.
- Dispatch model:
  - scheduler/control write either aggregate setpoints or equal per-phase splits depending on endpoint schema,
  - optional `trigger` pulses can be required to latch written targets.
- Dashboards:
  - private operator routes: `status`, `plots`, `manual-schedule`, `api-schedule`, `logs`,
  - public read-only routes: `status`, `plots`.

## In Scope
- Multi-thread agents: fetcher, scheduler, plant emulator, measurement, control engine, settings engine, private/public dashboards.
- API schedule fetch/post flows, posting retry queue, and runtime connection health.
- Per-plant recording to `data/YYYYMMDD_<plant>.csv` plus history loading/export.
- Plotting of total/day-ahead/mFRR schedule intent alongside measured telemetry.
- Local-start SoC seeding, estimated SoC fallback, and dispatch write status publication.

## Hard Constraints
- Holding-register Modbus only.
- Endpoint config must provide `byte_order`, `word_order`, and structured `points`.
- Setpoint schema must be exactly one family:
  - aggregate `p_setpoint` + `q_setpoint`, or
  - full per-phase sextet `p_[u|v|w]_setpoint` + `q_[u|v|w]_setpoint`.
- Optional trigger-latched endpoints must honor post-write `trigger` application when configured.
- Time handling must remain timezone-aware and normalized.
- Dispatch and settings operations remain queue-serialized.

## Success Criteria
1. Dispatch uses the correct `total` schedule and applies correctly on aggregate, per-phase, and trigger-latched endpoints.
2. LIB/VRFB schedule model remains uniform and internally consistent.
3. API Schedule page clearly exposes polling state, including mFRR poll telemetry.
4. Measurement files and history remain backward-compatible while preserving schedule-intent columns and usable SoC.
5. Operator/public dashboards remain menu-only and preserve existing controls and observability.
