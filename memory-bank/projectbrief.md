# Project Brief: HIL Scheduler

## Overview
HIL Scheduler is a multi-agent Python application that executes active and reactive power setpoints for two logical battery plants (`lib` and `vrfb`) using Modbus TCP. It supports local emulation and remote hardware transport, with a real-time dashboard for control and observability.

## Core Goals
1. Execute schedule setpoints at the configured scheduler cadence.
2. Maintain safe SoC-constrained battery behavior in local emulation.
3. Support manual and API schedule sources with controlled switching.
4. Record measurement sessions per plant/day for traceability.
5. Surface operational and API posting health in the dashboard.

## Runtime Model
- Logical plants: `lib`, `vrfb`.
- Global selectors:
  - `active_schedule_source`: `manual` or `api`.
  - `transport_mode`: `local` or `remote`.
- Per-plant controls:
  - dispatch gate via `scheduler_running_by_plant[plant_id]`.
  - recording gate via `measurements_filename_by_plant[plant_id]`.

## In Scope
- Threaded agents: director, data fetcher, scheduler, plant emulator, measurement, dashboard.
- Day-ahead API schedule ingestion and stale-setpoint guardrails.
- Per-plant Modbus endpoint management for local and remote modes.
- CSV measurement persistence and in-memory plot caches.
- API measurement posting with retry queue and observability state.

## Hard Constraints
- Power registers are 16-bit signed values encoded through two's complement (hW scale).
- Local emulation runs one Modbus server per logical plant simultaneously.
- Plant model limits come from `config.yaml`:
  - `lib`: 500 kWh, P +/-1000 kW, Q +/-600 kvar.
  - `vrfb`: 3000 kWh, P +/-3000 kW, Q +/-1200 kvar.
- Timestamps are timezone-aware in configured timezone (`time.timezone`).

## Success Criteria
1. Correct per-plant dispatch from selected schedule source.
2. Safe start/stop flows with explicit transition states.
3. Reliable per-plant recording files (`data/YYYYMMDD_<plant>.csv`).
4. Accurate API status (today/tomorrow windows, stale cutoff behavior).
5. Actionable dashboard visibility for API posting success/failure/queue state.
