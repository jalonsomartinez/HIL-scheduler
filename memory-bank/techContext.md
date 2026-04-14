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
- `modbus/setpoint_io.py`: aggregate/per-phase write planning, optional `q_control_mode`, and trigger-aware apply.

## Configuration Schema
Top-level keys remain:
- `general`, `time`, `schedule`, `startup`, `timing`, `dashboard`, `recording`, `istentore_api`, `plants`.

Plant schema now includes:
- `plants.<id>.model.voltage_control_droop_pu`
  - required if either transport endpoint for that plant defines `q_control_mode`,
  - stored in per-plant model config and exposed through resolved endpoints.
- `plants.<id>.modbus.{local,remote}.points.q_control_mode`
  - optional raw writable point,
  - used to select classic `Q` mode (`1`) or voltage mode (`3`).

Current config reality:
- LIB model now sets `voltage_control_droop_pu: 0.04`.
- LIB local and remote endpoints now declare `q_control_mode` at address `82`.

## Runtime Contracts Exposed by Config Loader
Important normalized keys include:
- `PLANTS`, `PLANT_IDS`
- `STARTUP_TRANSPORT_MODE`, `STARTUP_SCHEDULE_SOURCE`, `STARTUP_INITIAL_SOC_PU`
- `MEASUREMENT_COMPRESSION_ENABLED`, `MEASUREMENT_COMPRESSION_TOLERANCES`

Per-plant endpoint contracts now carry:
- `power_limits`
- `poi_voltage_kv`
- `voltage_control_droop_pu`
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

## Logging Behavior
- Global log level is config-driven via `general.log_level`.
- Scheduler dispatch logging now includes:
  - reactive control mode,
  - voltage-mode flag,
  - resolved `v_setpoint_pu`,
  - measured `v_poi_pu` when voltage mode is active.
- Twin-derived voltage references do not add a separate logging stream; they are visible through the resolved `v_setpoint_pu` carried in dispatch status, measurements, and dashboards.
- Control-path failures for voltage dispatch are surfaced explicitly instead of silently degrading to classic `Q` control.

## Operational Constraints
- Queue sizes are bounded and command execution remains serialized.
- Trigger-based apply remains synchronous and adds blocking latency on trigger-configured endpoints.
- Voltage regulation depends on a valid `v_poi` read from the active endpoint; missing reads are treated as dispatch failure in voltage mode.
- API password persistence is still process-memory only.
- `tests.test_local_runtime_smoke` still needs pooled-client adaptation.
