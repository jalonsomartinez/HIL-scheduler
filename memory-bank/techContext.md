# Technical Context: HIL Scheduler

## Technology Stack
- Language: Python 3.
- UI: Dash + Plotly.
- UI assets: tokenized CSS in `assets/custom.css` with local static brand assets under `assets/brand/`.
- Data processing: pandas, numpy.
- Modbus: pyModbusTCP client/server.
- Config: YAML via PyYAML.
- HTTP API integration: requests.

## Repository Runtime Modules
- `hil_scheduler.py`: director, shared state initialization, thread startup/shutdown.
- `config_loader.py`: validates/normalizes YAML into runtime dict.
- `dashboard_agent.py`: UI layout, callbacks, safe-stop controls, switch modals.
- `assets/custom.css`: dashboard design tokens, responsive rules, control/tab/modal/log styling.
- `assets/brand/fonts/*`: locally served dashboard fonts (DM Sans files + OFL license).
- `data_fetcher_agent.py`: day-ahead API polling and status updates.
- `scheduler_agent.py`: per-plant setpoint dispatch.
- `plant_agent.py`: local dual-server plant emulation.
- `measurement_agent.py`: sampling, recording, cache, API posting queue.
- `istentore_api.py`: API auth, schedule fetch, measurement post, and bounded token re-auth retry on `401`/`403`.
- `time_utils.py`: timezone normalization and serialization helpers.
- `logger_config.py`: console/file/session logging setup.

## Configuration Schema (Current)
`config.yaml` canonical keys:
- `general.log_level`
- `time.timezone`
- `schedule.source_csv`, `schedule.duration_h`, `schedule.default_resolution_min`
- `startup.schedule_source`, `startup.transport_mode`
- `timing.*_period_s`
- `istentore_api.*`
- `recording.compression.*`
- `plants.lib.*` and `plants.vrfb.*`

Per-plant config includes:
- `name`
- `model.capacity_kwh`, `model.initial_soc_pu`, `model.power_limits`, `model.poi_voltage_v`
- `modbus.local` and `modbus.remote` endpoints with register maps
- `measurement_series` IDs for `soc`, `p`, `q`, `v`

## Runtime Contracts Exposed by Config Loader
- `PLANTS`: normalized per-plant map.
- `PLANT_IDS`: `("lib", "vrfb")`.
- `STARTUP_SCHEDULE_SOURCE`, `STARTUP_TRANSPORT_MODE`.
- Timing/posting/settings flattened for agents (for example `SCHEDULER_PERIOD_S`, `ISTENTORE_*`).

## Modbus and Unit Conventions
- Power values are represented as signed 16-bit in hW (0.1 kW scale).
- Conversion helpers in `utils.py`:
  - `kw_to_hw`, `hw_to_kw`
  - `int_to_uint16`, `uint16_to_int`
- SoC register uses `pu * 10000`.
- Voltage register uses `pu * 100`.

## Plant Emulation Behavior
- Local mode starts one Modbus server per plant (`lib`, `vrfb`) simultaneously.
- Plant emulation applies:
  - enable gating,
  - configured active/reactive power limits,
  - SoC boundary limiting.
- POI active/reactive values mirror battery outputs in current simplified model.
- POI voltage per unit derives from configured plant voltage relative to 20 kV base.

## Logging Behavior
- Root logger has three outputs:
  1. console,
  2. date-routed file `logs/YYYY-MM-DD_hil_scheduler.log` (record timestamp date in configured timezone),
  3. in-memory session list (retained for compatibility and lightweight in-process diagnostics).
- Session logs are bounded to latest 1000 entries.
- Dashboard logs tab behavior:
  - default selector is `today`,
  - `today` reads tail of current date file for live refresh,
  - historical log browsing reads selectable files from `logs/*.log`.

## Dashboard Styling Conventions
- Brand assets are served from Dash `assets/` (logo PNGs + local font files).
- Dashboard visual state is class-driven in `dashboard_agent.py` and styled in `assets/custom.css` to minimize inline style drift.
- Plot styling in `dashboard_agent.py` uses shared figure-theme helpers for consistent axes/grid/legend presentation without altering control callbacks.
- Current operator-requested theme constraints:
  - flat corporate-green page background,
  - non-signature i-STENTORE logotype in header,
  - flat green/red control buttons,
  - higher-contrast toggle selected-state pill.

## Timezone and Persistence Conventions
- All runtime schedule/measurement timestamps are timezone-aware.
- Naive timestamps are interpreted in configured timezone when normalized.
- Measurement CSV uses ISO 8601 timestamps with timezone offset.
- API posting timestamps are normalized to UTC ISO format.

## Operational Constraints
- Threaded model requires short lock sections and external I/O outside locks.
- Measurement posting queue is in-memory only; it does not persist across restarts.
- The dashboard assumes both logical plants are always present in runtime state.
- API auth renewal is reactive (on `401`/`403`) with one retry per request path; no proactive token TTL refresh exists.
