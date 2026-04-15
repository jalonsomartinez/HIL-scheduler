# Active Context: HIL Scheduler

## Current Focus (Now)
- Field-validate the new direct plant voltage-control path on real hardware.
- Confirm `q_control_mode` + `v_setpoint` writes behave correctly on LIB local and remote endpoints.
- Validate the new standalone grid-map `v_poi_write` Modbus endpoint on both local and newly configured remote transport.
- Verify dashboard-selected `Q mode` / `V mode` behavior is usable and clearly communicates reactive-mode intent.
- Validate the new digital-twin voltage-reference fallback and its clamp behavior on realistic network states.
- Preserve telemetry/history compatibility while recording `v_setpoint_pu` in plant files and expanded digital-twin summary metrics in dedicated twin files.
- Validate the new shared `Grid Map / Digital Twin` historical plots in both private and public dashboards against real recorded data.
- Keep the current grid-map digital-twin audit trail intact during unrelated runtime work.

## Open Decisions and Risks
- `config.yaml` now carries assumed LIB `v_setpoint` register addresses that should be confirmed against the plant map before field use.
- Voltage-mode status surfaces `v_setpoint_pu`, but legacy `q_setpoint` register sampling remains unchanged and may still reflect stale plant state in `V mode`.
- The new remote standalone `grid_map.voltage_write_modbus.remote` endpoint still needs field validation on the target hardware path.
- Trigger apply timing remains synchronous and may be too slow for some endpoints.
- Per-phase-only endpoint configs are supported in scheduler/control dispatch, but local emulator write assumptions are still aggregate-oriented.
- API password remains process-memory only.
- Legacy plant CSVs can still contain embedded `grid_map_*` columns from the brief duplicated-history design, but the shared twin plot now ignores them and trusts only `*_twin.csv`.
- Historical twin plots remain empty until enough fresh `*_twin.csv` rows have been recorded.
- The April 2026 local pandapower edits for lines `841-848` remain investigative until technical-team review.

## Rolling Change Log (Compressed, 30-Day Window)
- 2026-04-14:
  - Added plant-level voltage-regulation dispatch support behind optional Modbus point `q_control_mode`.
  - Moved `v_poi_write` out of plant Modbus point maps into standalone `grid_map.voltage_write_modbus.{local,remote}` config.
  - Grid-map runtime now resolves one standalone voltage-write endpoint per active transport and writes once per cycle through that endpoint.
  - Standalone grid-map voltage-write transport reuse now relies on the shared Modbus client pool when `host + port` matches another runtime client.
  - Added remote standalone `v_poi_write` config in `config.yaml`.
  - Extended manual schedule/runtime state from four signals to six:
    - `lib_p`, `lib_q`, `lib_v`,
    - `vrfb_p`, `vrfb_q`, `vrfb_v`.
  - Voltage setpoint now defaults to `1.0 pu` when no manual voltage value is available.
  - Scheduler and control start flow now share voltage-aware dispatch-bundle resolution.
  - Voltage mode now writes `q_control_mode=3`, `P`, and plant `v_setpoint` directly; it no longer computes or writes `Q`.
  - Endpoints that expose `q_control_mode` must now also expose plant `v_setpoint`.
  - Removed plant-model droop config from runtime validation.
  - Classic reactive mode writes `q_control_mode=1` when configured.
  - Measurement rows/CSV/cache now record `v_setpoint_pu`.
  - Private/public status summaries now show `V ref`.
  - Reactive-mode selection is now owned by the dashboard `Q mode` / `V mode` toggle instead of manual-voltage activation.
  - Added digital-twin voltage-reference fallback for plants with `q_control_mode`, using `battery_voltage_pu + 1.0 - (max_voltage_pu + min_voltage_pu) / 2`, with final clamp to `[0.9, 1.1]`.
  - Grid Map summary cards now show battery voltage from the digital twin summary.
  - Verified targeted regression suites in the repo `venv`, covering config validation, schedule runtime, scheduler dispatch, measurement recording, dashboard intent wiring, shared-state contract, and control paths.
- 2026-04-15:
  - Shared digital-twin summary metrics and voltage-bucket node counts now persist to dedicated `data/YYYYMMDD_twin.csv` files instead of being duplicated into plant CSVs.
  - Grid-map runtime summary now publishes the seven voltage-bucket node counts used by history.
  - Private and public Plots pages now include a shared `Grid Map / Digital Twin` historical figure group above the per-plant charts.
  - Historical digital-twin plotting now reads only twin files and the Plots timeline includes LIB, VRFB, and Twin history tracks.
  - Verified targeted regression suites for grid-map runtime, measurement recording/compression, dashboard history/plotting, and private/public layout wiring.
- 2026-04-13:
  - Added shared SoC fallback estimation and optional `soc` schema support.
  - Added schema-aware aggregate vs per-phase dispatch and trigger-aware setpoint apply flow.
- 2026-04-10:
  - Corrected LIB battery reactive-power sign inversion in `grid_map_runtime.py`.
  - Preserved mirrored grid-map digital-twin investigative edits and audit notes.
