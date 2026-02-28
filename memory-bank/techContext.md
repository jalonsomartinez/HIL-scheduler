# Tech Context: HIL Scheduler

## Technology Stack
- Python 3.12 runtime.
- Dash + Plotly for private/public dashboards.
- Pandas for schedule and measurement shaping.
- Flask server under Dash.
- Threaded agent architecture, shared in-memory state.
- Modbus TCP via project Modbus helpers/codecs.

## Repository Runtime Modules
- `hil_scheduler.py`: process entrypoint, shared-state init, thread startup/shutdown.
- `config_loader.py`: strict schema normalization and runtime config map.
- `dashboard/`: layout, callbacks, plotting helpers, history helpers, logs tab.
- `control/`: command intents and execution flows (safe-stop, transport switch, fleet actions).
- `settings/`: API/manual settings intents and engine execution.
- `measurement/`: telemetry sampling, compression, persistence, posting queue.
- `scheduling/`: effective schedule merge + dispatch cycle logic.
- `modbus/`: endpoint config helpers, point codecs, unit handling, control-path I/O.
- `runtime/`: shared helpers for state defaults, command lifecycle, engine status, path resolution.

## Configuration Schema
Top-level keys in `config.yaml`:
- `general`, `time`, `schedule`, `startup`, `timing`, `dashboard`, `recording`, `istentore_api`, `plants`.
- `plants.<id>.modbus.{local,remote}` requires `host`, `port`, `byte_order`, `word_order`, and structured `points`.
- `startup.transport_mode`: `local|remote`.
- `dashboard.public_readonly.auth.mode`: `basic|none`.
- `istentore_api.tomorrow_poll_start_time`: `HH:MM` required format.

## Runtime Contracts Exposed by Config Loader
Important normalized keys include:
- Timing: `DATA_FETCHER_PERIOD_S`, `SCHEDULER_PERIOD_S`, `PLANT_PERIOD_S`, `MEASUREMENT_PERIOD_S`.
- Dashboard: `DASHBOARD_PRIVATE_*`, `DASHBOARD_PUBLIC_READONLY_*`.
- API: `ISTENTORE_*` fetch/post settings.
- Plant topology: `PLANTS`, `PLANT_IDS`.
- Startup behavior: `STARTUP_TRANSPORT_MODE`, `STARTUP_SCHEDULE_SOURCE`, `STARTUP_INITIAL_SOC_PU`.
- Recording compression: `MEASUREMENT_COMPRESSION_ENABLED`, tolerances, keep-gap threshold.

## Modbus and Unit Conventions
- Holding registers only.
- Point metadata includes `format`, `access`, `unit`, `eng_per_count`.
- Supported point formats include `int16`, `uint16`, `int32`, `uint32`, `float32`.
- Runtime measurements are normalized to engineering units (`kW`, `kvar`, `pu`, `kV`).
- Voltage is handled as `v_poi_kV` internally and in dashboard plots.

## Logging Behavior
- Global log level is config-driven (`general.log_level`).
- Session and file logging are available; dashboard includes logs tab for today/history files.
- Control/settings engines publish queue and exception status to shared runtime state for UI visibility.

## Operational Constraints
- Queue sizes are bounded (default 128 for control/settings commands).
- Serial command execution means long safe-stop/transport actions can delay later commands.
- Public dashboard can be auth-disabled (`none`) for trusted network use; basic auth requires env credentials.
- Network-restricted environments should validate API/posting behavior with posting policy disabled when needed.
