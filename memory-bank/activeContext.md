# Active Context: HIL Scheduler

## Current Focus (Now)
- Field-validate the new voltage-regulation reactive-control path on real hardware.
- Confirm optional `q_control_mode` writes behave correctly on LIB local and remote endpoints.
- Verify dashboard-selected `Q mode` / `V mode` behavior is usable and clearly communicates reactive-mode intent.
- Validate the new digital-twin voltage-reference fallback and its clamp behavior on realistic network states.
- Preserve telemetry/history compatibility while recording `v_setpoint_pu`.
- Keep the current grid-map digital-twin audit trail intact during unrelated runtime work.

## Open Decisions and Risks
- Voltage regulation currently uses `q_max_kvar` as the droop scaling base; field behavior may still motivate refinement.
- Voltage mode depends on live `v_poi` reads; noisy or unavailable voltage telemetry now causes explicit dispatch failure instead of fallback.
- Trigger apply timing remains synchronous and may be too slow for some endpoints.
- Per-phase-only endpoint configs are supported in scheduler/control dispatch, but local emulator write assumptions are still aggregate-oriented.
- API password remains process-memory only.
- The April 2026 local pandapower edits for lines `841-848` remain investigative until technical-team review.

## Rolling Change Log (Compressed, 30-Day Window)
- 2026-04-14:
  - Added plant-level voltage-regulation dispatch support behind optional Modbus point `q_control_mode`.
  - Added required plant config `model.voltage_control_droop_pu` whenever `q_control_mode` is configured.
  - Extended manual schedule/runtime state from four signals to six:
    - `lib_p`, `lib_q`, `lib_v`,
    - `vrfb_p`, `vrfb_q`, `vrfb_v`.
  - Voltage setpoint now defaults to `1.0 pu` when no manual voltage value is available.
  - Scheduler and control start flow now share voltage-aware dispatch-bundle resolution.
  - Voltage mode writes `q_control_mode=3` and computes `Q` from measured `v_poi`, plant nominal voltage, droop, and Q limits.
  - Classic reactive mode writes `q_control_mode=1` when configured.
  - Measurement rows/CSV/cache now record `v_setpoint_pu`.
  - Private/public status summaries now show `V ref`.
  - Reactive-mode selection is now owned by the dashboard `Q mode` / `V mode` toggle instead of manual-voltage activation.
  - Added digital-twin voltage-reference fallback for plants with `q_control_mode`, using `battery_voltage_pu + 1.0 - (max_voltage_pu + min_voltage_pu) / 2`, with final clamp to `[0.9, 1.1]`.
  - Grid Map summary cards now show battery voltage from the digital twin summary.
  - Verified targeted regression suites in the repo `venv`, covering config validation, schedule runtime, scheduler dispatch, measurement recording, dashboard intent wiring, shared-state contract, and control paths.
- 2026-04-13:
  - Added shared SoC fallback estimation and optional `soc` schema support.
  - Added schema-aware aggregate vs per-phase dispatch and trigger-aware setpoint apply flow.
- 2026-04-10:
  - Corrected LIB battery reactive-power sign inversion in `grid_map_runtime.py`.
  - Preserved mirrored grid-map digital-twin investigative edits and audit notes.
