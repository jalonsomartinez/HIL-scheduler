# Active Context: HIL Scheduler

## Current Focus (Now)
- Validate heterogeneous Modbus dispatch paths:
  - aggregate setpoints,
  - per-phase setpoints,
  - trigger-latched apply sequences.
- Keep SoC continuity credible when hardware omits a direct Modbus `soc` point.
- Continue stabilizing the three-layer schedule model (`day-ahead`, `mFRR`, `total`) for LIB and VRFB.
- Keep API-page observability high-signal, including runtime mFRR polling telemetry.
- Preserve backward-compatible history/CSV behavior while exposing schedule-intent columns.
- Keep menu-only private/public dashboard navigation stable.
- Preserve a clear audit trail for the current grid-map digital-twin investigation around transformer-header geometry/length data.

## Open Decisions and Risks
- Per-phase-only endpoint configs are accepted by schema and supported for scheduler/control writes, but measurement and local-emulator paths still assume aggregate setpoint telemetry.
- The default trigger apply timing is synchronous and currently adds about two seconds per successful apply; real-hardware acceptability still needs field validation.
- Trigger reset is now a hard gate before start on trigger-configured plants; whether that should remain strict or become configurable is still open.
- SoC estimation currently uses direct energy integration from battery active power; efficiency-aware tuning is not implemented.
- Scheduler readback still uses point-wise exact-word reads rather than grouped reads.
- Fake-client local smoke tests still need adaptation to pooled Modbus semantics.
- API password remains process-memory only.
- The April 2026 local pandapower model edits for lines `841-848` remain investigative until technical-team review.

## Rolling Change Log (Compressed, 30-Day Window)
- 2026-04-13:
  - Added `runtime/soc_estimation.py` as the shared startup SoC seed and fallback-estimation helper used by control, measurement, and plant emulation.
  - Made Modbus `soc` optional in schema validation.
  - Measurement runtime now keeps per-plant `SocEstimator` state:
    - sync to real SoC when present,
    - estimate from `battery_active_power_kw` when absent.
  - Local emulator SoC seed requests now still apply even when no `soc` register is configured.
  - Added schema-aware setpoint dispatch:
    - endpoints must provide either aggregate `p_setpoint`/`q_setpoint` or the full per-phase sextet,
    - scheduler/control build write plans from that schema,
    - per-phase mode splits totals equally across U/V/W points.
  - Added trigger-aware setpoint apply flow:
    - successful writes optionally pulse `trigger` high then low,
    - scheduler retries after trigger failure even when registers already match,
    - control-engine start flow resets `trigger` to `0` before prepare and fails early if reset fails.
  - Updated start-command prepare semantics so run-allowed writes now use `start_command=1` instead of `2`.
  - Expanded regression coverage for SoC fallback, config/schema validation, per-phase dispatch, and trigger-aware control/scheduler behavior.
- 2026-04-10:
  - Corrected LIB battery reactive-power sign inversion in `grid_map_runtime.py`.
  - Wrote `docs/audits/20260410_grid_map_geometry_findings.md` for dashboard grid-map voltage-drop and geometry findings.
  - Added mirrored backups of the original `line 843` digital-twin pickle state.
  - Applied mirrored investigative patches to `grid_map_digital_twin/net_digital_twin.p` and `digital_twin_package/net_digital_twin.p`:
    - lines `841-847` now use coordinate-derived lengths with a `10 m` minimum,
    - line `848` now uses its coordinate-derived length,
    - line `843` keeps original impedance-per-km values with shortened length and higher `max_i_ka`.
- 2026-03-27:
  - Replaced private/public dashboard tabs with menu-only route sections and added shared navigation helpers/tests.
- 2026-03-13:
  - Introduced split schedule maps (`day-ahead`, `mFRR`, `total`), dedicated mFRR cadence, schedule-intent history columns, and API-page mFRR polling telemetry.
