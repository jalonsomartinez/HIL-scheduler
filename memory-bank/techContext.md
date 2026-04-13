# Tech Context: HIL Scheduler

## Technology Stack
- Python 3.12 runtime.
- Dash + Plotly for private/public dashboards.
- Flask server under Dash.
- Pandas + NumPy for schedule, telemetry, and history shaping.
- Threaded agent architecture with shared in-memory state.
- Modbus TCP via project helpers/codecs.
- Runtime dependencies are pinned in `requirements.txt` for reproducible deployments (`dash==3.4.0`, `dash-auth==2.3.0`, `plotly==6.5.2`, `pandas==3.0.0`, `numpy==2.4.1`, `pyModbusTCP==0.3.0`, `pymodbus==3.9.2`, `PyYAML==6.0.3`).

## Repository Runtime Modules
- `hil_scheduler.py`: process entrypoint, shared-state init, thread startup/shutdown.
- `config_loader.py`: strict schema normalization and runtime config map.
- `dashboard/`: layouts, route/menu callbacks, plotting helpers, history helpers, logs page.
- `dashboard/navigation.py`: shared route normalization and menu/page-section helpers.
- `control/`: command intents, engine execution, safe-stop/transport/fleet flows.
- `settings/`: API/manual settings intents and engine execution.
- `measurement/`: telemetry sampling, compression, persistence, posting queue.
- `measurement/storage.py`: CSV normalization/load helpers and latest persisted per-plant SoC lookup.
- `scheduling/`: effective schedule merge and dispatch cycle logic.
- `modbus/`: endpoint config helpers, codecs, unit normalization, client pooling, grouped reads, and control-path I/O.
- `modbus/setpoint_io.py`: schema-aware aggregate/per-phase write planning, exact-word readback helpers, and optional trigger pulses.
- `runtime/`: defaults, contracts, paths, command lifecycle, engine status, shared-state helpers.
- `runtime/soc_estimation.py`: startup SoC seed resolution, clamping, and measurement-time SoC estimator.
- `grid_map_runtime.py`, `grid_map_digital_twin/`, `digital_twin_package/`: dashboard digital-twin state plus mirrored packaged assets.
- `scripts/`: local launchers and diagnostics helpers.

## Configuration Schema
Top-level keys in `config.yaml`:
- `general`, `time`, `schedule`, `startup`, `timing`, `dashboard`, `recording`, `istentore_api`, `plants`.
- `plants.<id>.modbus.{local,remote}` requires `host`, `port`, `byte_order`, `word_order`, and structured `points`.
- Endpoint point schema now accepts:
  - required read points: `p_battery`, `q_battery`, `enable`, `p_poi`, `q_poi`, `v_poi`,
  - optional `soc`,
  - either aggregate setpoints `p_setpoint` + `q_setpoint` or the full per-phase sextet,
  - optional raw/apply/control points such as `trigger`, `start_command`, `stop_command`, `v_poi_write`.
- `startup.transport_mode`: `local|remote`.
- `dashboard.public_readonly.auth.mode`: `basic|none`.
- `istentore_api.tomorrow_poll_start_time`: required `HH:MM`.
- `istentore_api.schedule_period_minutes`: scheduler API validity window.
- `istentore_api.mfrr_poll_period_s`: mFRR polling cadence (seconds, min 1).
- Optional env-derived API credential: `HIL_API_PASSWORD` -> `ISTENTORE_API_PASSWORD`.
- Optional Flask secret env: `HIL_FLASK_SECRET_KEY` (fallback alias `HIL_PUBLIC_DASH_SECRET_KEY`).

## Runtime Contracts Exposed by Config Loader
Important normalized keys include:
- Timing: `DATA_FETCHER_PERIOD_S`, `SCHEDULER_PERIOD_S`, `PLANT_PERIOD_S`, `MEASUREMENT_PERIOD_S`.
- Dashboard: `DASHBOARD_PRIVATE_*`, `DASHBOARD_PUBLIC_READONLY_*`.
- API: `ISTENTORE_*` fetch/post settings.
- mFRR cadence: `ISTENTORE_MFRR_POLL_PERIOD_S`.
- Plant topology: `PLANTS`, `PLANT_IDS`.
- Startup behavior: `STARTUP_TRANSPORT_MODE`, `STARTUP_SCHEDULE_SOURCE`, `STARTUP_INITIAL_SOC_PU`.
- Recording compression: `MEASUREMENT_COMPRESSION_ENABLED`, tolerances, keep-gap threshold.
- Credentials: `ISTENTORE_API_PASSWORD`.
- Schema normalization now enforces mutual exclusion between aggregate and per-phase setpoint families.

## Modbus and Unit Conventions
- Holding registers only.
- Point metadata includes `format`, `access`, `unit`, `eng_per_count`.
- Supported point formats: `int16`, `uint16`, `int32`, `uint32`, `float32`.
- Runtime measurements are normalized to engineering units (`kW`, `kvar`, `pu`, `kV`).
- Voltage is handled internally as `v_poi_kV`.
- Runtime backend prefers `pymodbus` with `pyModbusTCP` fallback.
- Runtime clients share per-endpoint transports to reduce multi-session contention.
- Grouped reads are used for stable read sets such as measurement and observed-state reads.
- Setpoint helpers support:
  - aggregate `p_setpoint` / `q_setpoint`,
  - per-phase `p_[u|v|w]_setpoint` / `q_[u|v|w]_setpoint` with equal total splitting.
- `trigger` is a raw holding-register point used as an optional apply pulse after successful setpoint writes.
- Recorded schedule-intent columns remain:
  - `p_schedule_total_kw`,
  - `p_schedule_day_ahead_kw`,
  - `p_schedule_mfrr_kw`.

## Logging Behavior
- Global log level is config-driven via `general.log_level`.
- Session and file logging are available; dashboard includes today/history log browsing.
- Control/settings engines publish queue and exception status to shared runtime state.
- Scheduler dispatch status now captures richer context around:
  - setpoint mode,
  - compare source (`readback` vs `cache_fallback`),
  - readback mismatch flags,
  - trigger failure vs setpoint failure outcomes.
- Data fetcher mFRR logs:
  - transition summaries at INFO,
  - unchanged steady-state summaries at DEBUG,
  - failures at ERROR.

## Operational Constraints
- Queue sizes are bounded (default 128 for control/settings commands).
- Serial command execution means long safe-stop/transport actions can delay later commands.
- Local emulator startup SoC restore scans plant CSV history; very large `data/` folders can increase startup latency.
- API password persistence is runtime-memory only.
- Pooled Modbus access introduces endpoint-level serialization; this reduces contention but can increase wait time under mixed workloads.
- Grouped reads are static/planned, not dynamically re-optimized per cycle.
- Trigger-based apply is synchronous; with the current helper timing (`1.0 s` before high and `1.0 s` before low), each successful triggered apply adds about two seconds of blocking latency to the caller path.
- Per-phase support is complete in scheduler/control write paths, but measurement sampling and local emulator loops still assume aggregate `p_setpoint` / `q_setpoint` telemetry when those paths are exercised.
- Network-restricted environments should validate API/posting behavior with posting policy disabled when needed.
- Cross-origin dashboard comparisons remain sensitive to dependency drift between servers.
- mFRR API coverage may not span the full 2-day horizon; LIB timestamps remain authoritative for VRFB mFRR alignment.
- The grid-map pandapower model is stored as pickled payloads; edits must preserve both mirrored copies and their backups.
