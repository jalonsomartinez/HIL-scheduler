# Tech Context: HIL Scheduler

## Technology Stack
- Python 3.12 runtime.
- Dash + Plotly for private/public dashboards.
- Pandas for schedule and measurement shaping.
- Flask server under Dash.
- Threaded agent architecture, shared in-memory state.
- Modbus TCP via project Modbus helpers/codecs.
- Direct runtime dependencies are pinned in `requirements.txt` for reproducible cross-server deployments (`dash==3.4.0`, `dash-auth==2.3.0`, `plotly==6.5.2`, `pandas==3.0.0`, `numpy==2.4.1`, `pyModbusTCP==0.3.0`, `pymodbus==3.9.2`, `PyYAML==6.0.3`).

## Repository Runtime Modules
- `hil_scheduler.py`: process entrypoint, shared-state init, thread startup/shutdown.
- `config_loader.py`: strict schema normalization and runtime config map.
- `dashboard/`: layout, callbacks, plotting helpers, history helpers, logs tab.
- `control/`: command intents and execution flows (safe-stop, transport switch, fleet actions).
- `settings/`: API/manual settings intents and engine execution.
- `measurement/`: telemetry sampling, compression, persistence, posting queue.
- `measurement/storage.py`: CSV normalization/load helpers and latest persisted per-plant SoC lookup.
- `scheduling/`: effective schedule merge + dispatch cycle logic.
- `modbus/`: endpoint config helpers, point codecs, unit handling, control-path I/O.
- `modbus/grouped_reads.py`: static grouped holding-register read planning/execution for stable point sets.
- `runtime/`: shared helpers for state defaults, command lifecycle, engine status, path resolution.
- `scripts/`: startup launchers for Linux/Windows that source local env files, activate `venv`, and run app.
- `scripts/vrfb_remote_diag.py`: remote diagnostics runner comparing dashboard-like and app-like access patterns.

## Configuration Schema
Top-level keys in `config.yaml`:
- `general`, `time`, `schedule`, `startup`, `timing`, `dashboard`, `recording`, `istentore_api`, `plants`.
- `plants.<id>.modbus.{local,remote}` requires `host`, `port`, `byte_order`, `word_order`, and structured `points`.
- `startup.transport_mode`: `local|remote`.
- `dashboard.public_readonly.auth.mode`: `basic|none`.
- `istentore_api.tomorrow_poll_start_time`: `HH:MM` required format.
- `istentore_api.schedule_period_minutes`: scheduler API validity window (minutes).
- `istentore_api.mfrr_poll_period_s`: mFRR polling cadence (seconds, min 1).
- Optional env-derived API credential: `HIL_API_PASSWORD` -> `ISTENTORE_API_PASSWORD`.
- Optional Flask secret env for dashboard session support: `HIL_FLASK_SECRET_KEY` (fallback alias `HIL_PUBLIC_DASH_SECRET_KEY`).

## Runtime Contracts Exposed by Config Loader
Important normalized keys include:
- Timing: `DATA_FETCHER_PERIOD_S`, `SCHEDULER_PERIOD_S`, `PLANT_PERIOD_S`, `MEASUREMENT_PERIOD_S`.
- Dashboard: `DASHBOARD_PRIVATE_*`, `DASHBOARD_PUBLIC_READONLY_*`.
- API: `ISTENTORE_*` fetch/post settings.
- mFRR polling cadence: `ISTENTORE_MFRR_POLL_PERIOD_S`.
- Plant topology: `PLANTS`, `PLANT_IDS`.
- Startup behavior: `STARTUP_TRANSPORT_MODE`, `STARTUP_SCHEDULE_SOURCE`, `STARTUP_INITIAL_SOC_PU`.
- Recording compression: `MEASUREMENT_COMPRESSION_ENABLED`, tolerances, keep-gap threshold.
- Credentials: `ISTENTORE_API_PASSWORD` (optional preload for runtime `api_password`).

## Modbus and Unit Conventions
- Holding registers only.
- Point metadata includes `format`, `access`, `unit`, `eng_per_count`.
- Supported point formats include `int16`, `uint16`, `int32`, `uint32`, `float32`.
- Runtime measurements are normalized to engineering units (`kW`, `kvar`, `pu`, `kV`).
- Voltage is handled as `v_poi_kV` internally and in dashboard plots.
- Runtime Modbus client backend is `pymodbus` when available (pinned `pymodbus==3.9.2`), with `pyModbusTCP` fallback.
- Runtime clients now share per-endpoint transports to reduce multi-session contention.
- Grouped reads are used for stable read sets (measurement and observed-state) to reduce request count.
- Recorded schedule intent columns use active-power kW units:
  - `p_schedule_total_kw`,
  - `p_schedule_day_ahead_kw`,
  - `p_schedule_mfrr_kw`.

## Logging Behavior
- Global log level is config-driven (`general.log_level`).
- Session and file logging are available; dashboard includes logs tab for today/history files.
- Control/settings engines publish queue and exception status to shared runtime state for UI visibility.
- Data fetcher mFRR logs:
  - transition-level poll summaries at INFO,
  - unchanged steady-state poll summaries at DEBUG,
  - failures at ERROR.

## Operational Constraints
- Queue sizes are bounded (default 128 for control/settings commands).
- Serial command execution means long safe-stop/transport actions can delay later commands.
- Local emulator startup SoC restore scans plant CSV history; very large `data/` folders may increase startup latency without indexing/caching.
- Public dashboard can be auth-disabled (`none`) for trusted network use; basic auth requires env credentials.
- API tab separates password save from connect/disconnect; password persistence is runtime-memory only.
- Network-restricted environments should validate API/posting behavior with posting policy disabled when needed.
- Pooled Modbus access introduces endpoint-level serialization; this reduces contention but can increase wait time under heavy mixed workloads.
- Grouped reads are static/planned, not dynamically re-optimized per cycle.
- Cross-origin dashboard comparisons are sensitive to environment drift: even with identical browser/version, mismatched server dependency versions can produce different Dash component rendering.
- mFRR API window may not cover full 2-day horizon; runtime now treats returned LIB mFRR timestamps as authoritative and aligns VRFB mFRR to that index.
