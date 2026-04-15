# Progress: HIL Scheduler

## Working Now
- Validating direct plant voltage control on top of the existing heterogeneous Modbus dispatch stack.
- Confirming `q_control_mode` + `v_setpoint` behavior on the currently configured LIB endpoint set.
- Validating the standalone grid-map `v_poi_write` endpoint migration, including the newly configured remote transport target.
- Verifying `v_setpoint_pu` continuity through measurement, cache, CSV, status summaries, and digital-twin fallback.
- Validating the dedicated twin-history files and shared historical Grid Map plots against real recorded sessions.
- Keeping trigger-aware apply and SoC fallback behavior stable after the dispatch expansion.

## In Progress
1. Reactive-control expansion
  - optional `q_control_mode` support
  - endpoint-level `v_setpoint` validation
  - scheduler/control direct voltage-mode dispatch
2. Manual schedule expansion
   - six manual channels including voltage
   - shared draft/apply/update workflow
   - private dashboard voltage controls
3. Measurement/observability alignment
  - `v_setpoint_pu` in measurement rows and CSV
  - `V ref` in private/public status tables
  - digital-twin battery voltage in Grid Map summary
  - digital-twin summary metrics and voltage-bucket node counts in dedicated twin-history files
  - shared `Grid Map / Digital Twin` historical plots in private/public dashboards
  - richer dispatch-write context for voltage mode
4. Voltage-reference source expansion
  - manual voltage override remains highest priority
  - digital-twin voltage reference feeds eligible plants when manual voltage is absent
  - corrected twin formula uses battery voltage plus average of global min/max voltage
5. Grid-map Modbus hardening
  - `v_poi_write` moved to standalone `grid_map.voltage_write_modbus.{local,remote}`
  - plant point maps now reject `v_poi_write`
  - standalone write path reuses pooled Modbus transport automatically on matching `host + port`
6. Existing hardening streams
  - trigger-aware apply sequencing
  - SoC estimation fallback
  - mFRR observability

## Next
1. Field-test voltage mode on LIB local and remote endpoints that now expose `q_control_mode`.
2. Confirm actual plant `v_setpoint` register addresses and field response under realistic voltage excursions with twin-derived `V ref`.
3. Confirm the corrected twin formula and clamp produce acceptable field behavior before adding more voltage-reference sources.
4. Confirm that the shared digital-twin history plots stay coherent across day rollover and start/stop boundaries in `*_twin.csv`.
5. Continue remote Modbus reliability hardening and pooled-client smoke-test adaptation.
6. Confirm with the technical team whether transformer-header lines `841-848` should keep the current investigative geometry-based values.

## Known Issues / Gaps
1. No durable persistence for the measurement-post retry queue across process restarts.
2. Serialized command execution can delay later commands during long safe-stop/transport sequences.
3. Manual schedule drafts are shared in server state (single-operator assumption).
4. UI regressions are still caught mainly by targeted tests and manual review.
5. API password is process-memory only.
6. `tests.test_local_runtime_smoke` still fails under pooled Modbus fake-client patching.
7. `config.yaml` contains assumed LIB `v_setpoint` addresses that still need plant-map confirmation.
8. Trigger-latched setpoint apply still blocks for about two seconds per successful apply with default helper timing.
9. The current grid-map pandapower pickle contains investigative edits for transformer-header lines `841-848` that are documented but not yet validated as final network parameters.
10. The new remote standalone `grid_map.voltage_write_modbus` target is configured but still needs field validation.
11. Historical twin plots remain empty until new `*_twin.csv` rows exist for the selected range.

## Current Project Phase
Heterogeneous Modbus dispatch hardening with voltage-regulation rollout, alongside SoC continuity support, schedule observability work, and grid-map digital-twin audit follow-up.
