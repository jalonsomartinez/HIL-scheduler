# Project Brief: HIL Scheduler

## Overview
HIL Scheduler is a dual-plant control runtime for LIB and VRFB batteries. It combines API market schedules with optional manual overrides, dispatches Modbus setpoints, records telemetry, and serves private/public dashboards for operators and observers.

## Core Goals
1. Dispatch active and reactive power safely across heterogeneous Modbus endpoints.
2. Support both classic reactive-power control and direct plant voltage control where plant interfaces expose it.
3. Keep plant transitions safe (`start`, `stop`, `transport switch`) through queue-serialized control flows.
4. Maintain one consistent schedule model across LIB and VRFB, with `total = day-ahead + mFRR`.
5. Preserve high-quality telemetry, history export, and usable SoC even when hardware omits a direct SoC register.

## Runtime Model
- Logical plants: `lib`, `vrfb`.
- Transport modes: `local`, `remote`.
- Modbus model: shared per-endpoint transport with serialized execution and grouped reads for stable read sets.
- Schedule model:
  - `day-ahead` from market id 4,
  - `mFRR` from market id 3 on its own cadence,
  - `total` authoritative dispatch schedule,
  - manual per-signal overrides for `P`, `Q`, and voltage setpoint,
  - digital-twin fallback voltage reference for plants whose active endpoint exposes `q_control_mode`.
- Dispatch model:
  - scheduler/control write aggregate or equal per-phase setpoints depending on endpoint schema,
  - optional `q_control_mode` selects classic `Q` mode or direct `V` mode when configured,
  - direct voltage mode writes plant `v_setpoint` using physical voltage units derived from runtime `pu`,
  - optional `trigger` pulses can latch written targets.

## In Scope
- Multi-thread agents: fetcher, scheduler, plant emulator, measurement, control engine, settings engine, private/public dashboards.
- API schedule fetch/post flows, posting retry queue, and runtime connection health.
- Per-plant recording to `data/YYYYMMDD_<plant>.csv`, shared digital-twin recording to `data/YYYYMMDD_twin.csv` and `data/YYYYMMDD_twin_nobat.csv`, plus history loading/export.
- Manual schedule editing for active power, reactive power, and voltage setpoint.
- Local-start SoC seeding, estimated SoC fallback, and dispatch write status publication.
- Parallel digital-twin scenarios:
  - with live battery `P/Q`,
  - with battery removed (`P=0`, `Q=0`),
  - plus summary persistence in dedicated twin-history files and shared historical comparison plots.

## Hard Constraints
- Holding-register Modbus only.
- Endpoint config must provide `byte_order`, `word_order`, and structured `points`.
- Setpoint schema must be exactly one family:
  - aggregate `p_setpoint` + `q_setpoint`, or
  - full per-phase sextet `p_[u|v|w]_setpoint` + `q_[u|v|w]_setpoint`.
- Any endpoint that declares `q_control_mode` must also declare `v_setpoint`.
- Dispatch and settings operations remain queue-serialized.

## Success Criteria
1. Dispatch uses the correct `total` schedule and applies correctly on aggregate, per-phase, trigger-latched, and optional voltage-regulation endpoints.
2. Reactive mode selection and voltage-reference sourcing stay coherent:
  - dashboard-selected `Q`/`V` mode dispatches correctly,
  - `V` mode writes plant `v_setpoint` directly instead of synthesizing `Q`,
  - manual voltage overrides twin-derived voltage reference when present,
  - fallback remains bounded and safe.
3. Measurement/history remain backward-compatible while preserving plant schedule-intent columns, `v_setpoint_pu`, and dedicated twin-history files.
4. Operator/public dashboards preserve existing controls while exposing `V ref` clearly, the Grid Map page can switch instantly between with-battery and no-battery scenarios, and the Plots page shows with-battery, no-battery, and signed-impact twin history from the dedicated twin files.
5. Core control, safe-stop, and transport behavior remain stable in local and remote operation.
