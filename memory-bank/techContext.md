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
- `dashboard_agent.py`: UI layout, callbacks, safe-stop controls, switch modals, fleet start/stop actions, and API posting toggle handling.
- `dashboard_history.py`: historical plots helper utilities (file scan/index, slider range helpers, CSV crop/export serialization).
- `dashboard_control.py`: safe-stop/source-switch/transport-switch control-flow helpers for dashboard callbacks.
- `assets/custom.css`: dashboard design tokens, responsive rules, control/tab/modal/log styling.
- `assets/brand/fonts/*`: locally served dashboard fonts (DM Sans files + OFL license).
- `data_fetcher_agent.py`: day-ahead API polling and status updates.
- `scheduler_agent.py`: per-plant setpoint dispatch.
- `plant_agent.py`: local dual-server plant emulation.
- `measurement_agent.py`: sampling, recording, cache, API posting queue.
- `measurement_storage.py`: measurement normalization, CSV read/write helpers, latest persisted per-plant SoC lookup helper, and row-similarity primitives for compression.
- `istentore_api.py`: API auth, schedule fetch, measurement post, and bounded token re-auth retry on `401`/`403`.
- `time_utils.py`: timezone normalization and serialization helpers.
- `logger_config.py`: console/file/session logging setup.

## Configuration Schema (Current)
`config.yaml` canonical keys:
- `general.log_level`
- `time.timezone`
- `schedule.source_csv`, `schedule.duration_h`, `schedule.default_resolution_min`
- `startup.schedule_source`, `startup.transport_mode`, `startup.initial_soc_pu`
- `timing.*_period_s`
- `istentore_api.*`
- `recording.compression.*`
- `plants.lib.*` and `plants.vrfb.*`

Notes:
- `schedule.*` is parsed by `config_loader.py`; active scheduler dispatch uses in-memory per-plant schedule maps and does not consume these keys directly.
- `istentore_api.tomorrow_poll_start_time` is the canonical next-day polling gate key for API day-ahead fetches; legacy `istentore_api.poll_start_time` is intentionally rejected (breaking rename, no alias).
- `recording.compression.*` is parsed and applied by `measurement_agent.py` for tolerance-based in-memory row compaction, configurable keep-gap retention (`max_kept_gap_s`), and periodic flush tail retention.
- Legacy flat alias keys from `config_loader.py` are disabled by default and are only emitted when `HIL_ENABLE_LEGACY_CONFIG_ALIASES=1`.

Per-plant config includes:
- `name`
- `model.capacity_kwh`, `model.power_limits`, `model.poi_voltage_kv`
- `modbus.local` and `modbus.remote` endpoints with required connection ordering (`byte_order`, `word_order`)
- endpoint `points` map entries (holding-register-only) with explicit `address`, `format`, `access`, `unit`, `eng_per_count`
- canonical point keys include `p_setpoint`, `q_setpoint`, `p_battery`, `q_battery`, `enable`, `soc`, `p_poi`, `q_poi`, `v_poi` (optional command points such as `start_command` / `stop_command` are preserved if present)
- `measurement_series` IDs for `soc`, `p`, `q`, `v`

## Runtime Contracts Exposed by Config Loader
- `PLANTS`: normalized per-plant map.
- `PLANTS[*].modbus.*` exposes endpoint `host`, `port`, `byte_order`, `word_order`, and normalized `points`.
- `PLANT_IDS`: `("lib", "vrfb")`.
- `STARTUP_SCHEDULE_SOURCE`, `STARTUP_TRANSPORT_MODE`.
- Timing/posting/settings flattened for agents (for example `SCHEDULER_PERIOD_S`, `ISTENTORE_*`).
- API fetcher next-day poll gate is exposed as normalized `ISTENTORE_TOMORROW_POLL_START_TIME` (`HH:MM`).
- Recording compression settings are flattened for agents, including `MEASUREMENT_COMPRESSION_ENABLED`, `MEASUREMENT_COMPRESSION_TOLERANCES`, and `MEASUREMENT_COMPRESSION_MAX_KEPT_GAP_S`.

## Modbus and Unit Conventions
- Modbus runtime contract is schema-driven per endpoint/point (`config.yaml`) and uses holding registers only.
- Endpoint-level ordering is required (`byte_order`, `word_order`) and shared by all points on that endpoint (no loader defaults).
- Shared codec implementation lives in `modbus_codec.py`; unit conversion between Modbus engineering values and internal runtime units is handled by `modbus_units.py`.
- Runtime Modbus read/write helpers now apply both:
  - point binary codec (`format`, `eng_per_count`)
  - point unit conversion (`unit`) to/from internal units
- Internal runtime units:
  - SoC: `pu`
  - active power: `kW`
  - reactive power: `kvar`
  - voltage: `kV` (measurement field `v_poi_kV`)
- Current configured point formats/scales:
  - `p_*` / `q_*` power and setpoint points: `int16`, `eng_per_count=0.1` (`kW` / `kvar`)
  - `soc`: `uint16`, `eng_per_count=0.0001` (`pu`)
  - `v_poi`: `uint16`, configured as absolute voltage (`V`/`kV`) and converted to internal `kV`
  - `enable`, `start_command`, `stop_command`: `uint16`, `eng_per_count=1.0` (`raw`)
- Supported point unit tokens are case-insensitive and normalized (for example `%` -> `pc`, `kW` -> `kw`, `Mvar` -> `mvar`, `kV` -> `kv`).

## Plant Emulation Behavior
- Local mode starts one Modbus server per plant (`lib`, `vrfb`) simultaneously.
- Local plant start in dashboard local transport now attempts to restore emulator SoC from the latest persisted on-disk `soc_pu` for that plant (`data/YYYYMMDD_<sanitized_plant>.csv`), with fallback to `STARTUP_INITIAL_SOC_PU`.
- Dashboard->plant-agent coordination for this restore uses shared-state request/ack maps; `plant_agent.py` applies the seed to internal emulator state (`soc_kwh`) before enable.
- Plant emulation applies:
  - enable gating,
  - configured active/reactive power limits,
  - SoC boundary limiting.
- POI active/reactive values mirror battery outputs in current simplified model.
- POI voltage is simulated as an absolute constant in internal `kV` using `plants.*.model.poi_voltage_kv`, then encoded to the configured Modbus point unit (`V` or `kV`).

## Logging Behavior
- Root logger has three outputs:
  1. console,
  2. date-routed file `logs/YYYY-MM-DD_hil_scheduler.log` (record timestamp date in configured timezone),
  3. in-memory session list (retained for compatibility and lightweight in-process diagnostics).
- Session logs are bounded to latest 1000 entries.
- `data_fetcher_agent.py` logs explicit API fetch intent (`today` vs `tomorrow`), local request windows, and next-day gate state transitions (`waiting` / `eligible`) to reduce ambiguity around missing schedules.
- Dashboard logs tab behavior:
  - default selector is `today`,
  - `today` reads tail of current date file for live refresh,
  - historical log browsing reads selectable files from `logs/*.log`.

## Dashboard Styling Conventions
- Brand assets are served from Dash `assets/` (logo PNGs + local font files).
- Dashboard visual state is primarily class-driven in `dashboard_agent.py` and styled in `assets/custom.css`; a small number of inline style dictionaries remain in log/posting render helpers.
- Plot styling in `dashboard_agent.py` uses shared figure-theme helpers for consistent axes/grid/legend presentation without altering control callbacks.
- Historical `Plots` tab reuses the same figure helper/theme as Status plots for visual consistency; PNG downloads use client-side Plotly export (`window.Plotly.downloadImage`) and do not require `kaleido`.
- Current operator-requested theme constraints:
  - white page background,
  - flatter surfaces with minimal corner radius,
  - menu-style tab strip (no enclosing tab-shell card),
  - tab content cards aligned to tab strip width (no side margins),
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
- Local SoC restore handshake is best-effort by design: dashboard start waits briefly for plant-agent ack and logs timeout, but still proceeds with plant enable/start sequence.
- Measurement posting queue is in-memory only; it does not persist across restarts.
- Measurement compression applies only to new runtime writes; no automatic backfill is performed for historical dense CSV files.
- Historical plots tab reads `data/*.csv` directly on demand; large datasets may increase dashboard callback latency because there is no persistent history index/cache yet.
- The dashboard assumes both logical plants are always present in runtime state.
- API auth renewal is reactive (on `401`/`403`) with one retry per request path; no proactive token TTL refresh exists.
