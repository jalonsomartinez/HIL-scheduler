# Tech Context: HIL Scheduler

## Technology Stack
- Python 3.12 runtime.
- Dash + Plotly for private/public dashboards.
- Pandas + NumPy for schedule, telemetry, and history shaping.
- Threaded agent architecture with shared in-memory state.
- Modbus TCP via project helpers/codecs.

## Repository Runtime Modules
- `config_loader.py`: strict schema normalization and runtime config map.
- `scheduling/manual_schedule_manager.py`: manual-series metadata, editor serialization, and derived per-plant schedule rebuilding.
- `scheduling/runtime.py`: shared dispatch-bundle resolution, staleness handling, and effective-schedule helpers.
- `scheduling/agent.py`: periodic dispatch loop and scheduler write-status publication.
- `control/modbus_io.py` and `control/engine_agent.py`: control-path Modbus writes and start/stop flows.
- `measurement/agent.py` and `measurement/storage.py`: telemetry sampling, schedule-intent enrichment, CSV/cache normalization.
- `dashboard/`: manual schedule UI, grid-map summary cards, status summaries, plots, and read-only public views.
- `grid_map_runtime.py`: digital-twin execution, summary extraction, and shared grid-map runtime snapshot contract.
- `modbus/setpoint_io.py`: aggregate/per-phase write planning, optional `q_control_mode`, optional plant `v_setpoint`, and trigger-aware apply.

## Configuration Schema
Top-level keys remain:
- `general`, `time`, `schedule`, `startup`, `timing`, `dashboard`, `grid_map`, `recording`, `istentore_api`, `plants`.

Standalone grid-map config now includes:
- `grid_map.voltage_write_modbus.{local,remote}`
  - optional full Modbus endpoint schema,
  - same endpoint fields as plant transports: `host`, `port`, `byte_order`, `word_order`, `points`,
  - exactly one supported point: `v_poi_write`,
  - missing `local` or `remote` subsection means voltage write is disabled for that transport.

Plant schema now includes:
- `plants.<id>.modbus.{local,remote}.points.q_control_mode`
  - optional raw writable point,
  - used to select classic `Q` mode (`1`) or voltage mode (`3`).
- `plants.<id>.modbus.{local,remote}.points.v_setpoint`
  - required on any endpoint that exposes `q_control_mode`,
  - stores the direct plant voltage target in physical `V` or `kV`.

Current config reality:
- LIB local and remote endpoints now declare `q_control_mode` at address `82`.
- LIB local and remote endpoints now declare plant `v_setpoint` points for direct voltage mode.
- `config.yaml` now configures standalone grid-map `v_poi_write` endpoints for both local and remote transport under `grid_map.voltage_write_modbus`.

## Runtime Contracts Exposed by Config Loader
Important normalized keys include:
- `PLANTS`, `PLANT_IDS`
- `STARTUP_TRANSPORT_MODE`, `STARTUP_SCHEDULE_SOURCE`, `STARTUP_INITIAL_SOC_PU`
- `MEASUREMENT_COMPRESSION_ENABLED`, `MEASUREMENT_COMPRESSION_TOLERANCES`
- `GRID_MAP_VOLTAGE_WRITE_MODBUS`

Per-plant endpoint contracts now carry:
- `power_limits`
- `poi_voltage_kv`
- normalized `points`

Grid-map runtime snapshot now exposes a summary contract used outside the map page:
- `battery_voltage_pu`
- `min_voltage_pu`
- `max_voltage_pu`
- `num_voltage_violations`
- `max_line_loading_pct`
- `num_overloaded_lines`

## Modbus and Unit Conventions
- Holding registers only.
- Supported point formats: `int16`, `uint16`, `int32`, `uint32`, `float32`.
- Runtime measurements are normalized to engineering units:
  - active power: `kW`
  - reactive power: `kvar`
  - voltage measurement: `kV`
  - voltage reference: `pu`
- Raw/control points use `unit: raw`, including `trigger` and `q_control_mode`.
- Plant `v_setpoint` uses voltage units (`V`/`kV`) and runtime converts from `pu` using `poi_voltage_kv`.

## Logging Behavior
- Global log level is config-driven via `general.log_level`.
- Scheduler dispatch logging now includes:
  - reactive control mode,
  - write quantity mode (`pq` or `pv`),
  - voltage-mode flag,
  - resolved `v_setpoint_pu`.
- Twin-derived voltage references do not add a separate logging stream; they are visible through the resolved `v_setpoint_pu` carried in dispatch status, measurements, and dashboards.
- Grid-map voltage-write logging now references the standalone endpoint selected by active transport instead of plant IDs.
- Control-path failures for voltage dispatch are surfaced explicitly instead of silently degrading to classic `Q` control.

## Operational Constraints
- Queue sizes are bounded and command execution remains serialized.
- Trigger-based apply remains synchronous and adds blocking latency on trigger-configured endpoints.
- Voltage mode depends on endpoints exposing both `q_control_mode` and `v_setpoint`; unsupported endpoints are rejected before dispatch.
- API password persistence is still process-memory only.
- `tests.test_local_runtime_smoke` still needs pooled-client adaptation.
