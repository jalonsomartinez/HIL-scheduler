# Project Brief: HIL Scheduler

## Overview
HIL Scheduler is a dual-plant control application for LIB and VRFB battery assets. It ingests API schedules, applies optional manual overrides, dispatches Modbus setpoints, records telemetry, and provides operator and public dashboards.

## Core Goals
1. Dispatch active/reactive setpoints safely and on cadence.
2. Keep plant transitions safe (`start`, `stop`, `transport switch`) via queued control flows.
3. Separate plant power state from dispatch sending and recording state.
4. Persist per-plant measurements with compression and export-friendly history.
5. Provide clear status observability for API, control engine, queue health, and last writes.

## Runtime Model
- Logical plants: `lib`, `vrfb`.
- Transport modes: `local`, `remote`.
- Schedule model: API base schedule plus per-series manual overrides (`lib_p`, `lib_q`, `vrfb_p`, `vrfb_q`) with active/inactive merge flags.
- Control model: dashboard enqueues commands; control/settings engines execute them and publish runtime state.
- Dashboards:
  - Private operator dashboard: full controls.
  - Public dashboard: read-only status/plots.

## In Scope
- Multi-thread agents: data fetcher, scheduler, plant emulator, measurement, control engine, settings engine, operator dashboard, public dashboard.
- Modbus endpoint handling for each plant in local and remote transport.
- API fetch/post flows, posting retry queue, and API connection runtime state.
- Historical plotting from CSV files with range selection and export.

## Hard Constraints
- Holding-register Modbus only.
- Endpoint `byte_order` and `word_order` are required.
- Structured `plants.*.modbus.{local,remote}.points` config schema is required.
- Time handling is timezone-aware using configured timezone.
- Dispatch and settings commands are serialized through bounded queues.

## Success Criteria
1. Correct merged dispatch behavior for API + enabled manual overrides.
2. Reliable safe-stop and transport-switch behavior under normal and failure paths.
3. Stable recording files (`data/YYYYMMDD_<plant>.csv`) with consistent units.
4. Operator UI clearly shows control states and last measurement snapshots.
5. Public dashboard provides accurate read-only status and historical visibility.
