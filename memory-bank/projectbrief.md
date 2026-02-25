# Project Brief: HIL Scheduler

## Overview
HIL Scheduler is a multi-agent Python application that executes active and reactive power setpoints for two logical battery plants (`lib` and `vrfb`) using Modbus TCP. It supports local emulation and remote hardware transport, with a real-time dashboard for control and observability.

## Core Goals
1. Execute schedule setpoints at the configured scheduler cadence.
2. Maintain safe SoC-constrained battery behavior in local emulation.
3. Dispatch from an API base schedule with optional per-signal manual overrides (`P`/`Q` for `lib` and `vrfb`).
4. Record measurement sessions per plant/day with boundary-preserving compression for traceability.
5. Surface operational and API posting health in the dashboard.

## Runtime Model
- Logical plants: `lib`, `vrfb`.
- Global selectors:
  - `transport_mode`: `local` or `remote`.
  - `measurement_posting_enabled`: runtime API-posting gate (`True`/`False`, session-scoped, default from config).
- Manual override model:
  - authoritative manual series storage per key: `lib_p`, `lib_q`, `vrfb_p`, `vrfb_q`,
  - per-series active/inactive merge toggle controls whether that manual series overwrites the API base for dispatch.
- Per-plant controls:
  - dispatch gate via `scheduler_running_by_plant[plant_id]`.
  - recording gate via `measurements_filename_by_plant[plant_id]`.
  - operator control intents are enqueued by the dashboard and executed by a runtime control engine.

## In Scope
- Threaded agents: director, data fetcher, scheduler, plant emulator, measurement, control engine, dashboard.
- Day-ahead API schedule ingestion and stale-setpoint guardrails.
- Per-plant Modbus endpoint management for local and remote modes.
- CSV measurement persistence and in-memory plot caches.
- Dashboard historical measurement browsing from `data/*.csv` with range filtering and exports.
- API measurement posting with retry queue and observability state.
- Dashboard fleet controls (`Start All`/`Stop All`) and confirmation-gated high-impact actions.

## Hard Constraints
- Modbus I/O uses holding registers only.
- Endpoint `byte_order` and `word_order` are required in config (no loader defaults).
- Current configured power points use signed 16-bit values encoded via two's complement at hW scale (`0.1` kW/kvar per count).
- Local emulation runs one Modbus server per logical plant simultaneously.
- Plant model limits come from `config.yaml`:
  - `lib`: 500 kWh, P +/-1000 kW, Q +/-600 kvar.
  - `vrfb`: 400 kWh, P +/-160 kW, Q +/-64 kvar.
- Timestamps are timezone-aware in configured timezone (`time.timezone`).

## Success Criteria
1. Correct per-plant dispatch from merged effective schedule (API base + enabled manual overrides).
2. Safe start/stop flows with explicit transition states.
3. Reliable per-plant recording files (`data/YYYYMMDD_<plant>.csv`).
4. Accurate API status (today/tomorrow windows, stale cutoff behavior).
5. Actionable dashboard visibility for API posting success/failure/queue state.
6. Operators can browse historical measurements across the `data/` time span and export cropped CSV/PNG per plant.
