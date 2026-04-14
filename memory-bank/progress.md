# Progress: HIL Scheduler

## Working Now
- Validating voltage-regulation reactive control on top of the existing heterogeneous Modbus dispatch stack.
- Confirming `q_control_mode` behavior on the currently configured LIB endpoint set.
- Verifying `v_setpoint_pu` continuity through measurement, cache, CSV, status summaries, and digital-twin fallback.
- Keeping trigger-aware apply and SoC fallback behavior stable after the dispatch expansion.

## In Progress
1. Reactive-control expansion
   - optional `q_control_mode` support
   - plant-level droop config validation
   - scheduler/control voltage-mode dispatch
2. Manual schedule expansion
   - six manual channels including voltage
   - shared draft/apply/update workflow
   - private dashboard voltage controls
3. Measurement/observability alignment
  - `v_setpoint_pu` in measurement rows and CSV
  - `V ref` in private/public status tables
  - digital-twin battery voltage in Grid Map summary
  - richer dispatch-write context for voltage mode
4. Voltage-reference source expansion
  - manual voltage override remains highest priority
  - digital-twin voltage reference feeds eligible plants when manual voltage is absent
  - corrected twin formula uses battery voltage plus average of global min/max voltage
5. Existing hardening streams
  - trigger-aware apply sequencing
  - SoC estimation fallback
  - mFRR observability

## Next
1. Field-test voltage mode on LIB local and remote endpoints that now expose `q_control_mode`.
2. Confirm sign, droop magnitude, and plant response under realistic voltage excursions with twin-derived `V ref`.
3. Confirm the corrected twin formula and clamp produce acceptable field behavior before adding more voltage-reference sources.
4. Continue remote Modbus reliability hardening and pooled-client smoke-test adaptation.
5. Confirm with the technical team whether transformer-header lines `841-848` should keep the current investigative geometry-based values.

## Known Issues / Gaps
1. No durable persistence for the measurement-post retry queue across process restarts.
2. Serialized command execution can delay later commands during long safe-stop/transport sequences.
3. Manual schedule drafts are shared in server state (single-operator assumption).
4. UI regressions are still caught mainly by targeted tests and manual review.
5. API password is process-memory only.
6. `tests.test_local_runtime_smoke` still fails under pooled Modbus fake-client patching.
7. Voltage mode currently hard-fails when `v_poi` is unreadable; there is no degraded fallback policy.
8. Trigger-latched setpoint apply still blocks for about two seconds per successful apply with default helper timing.
9. The current grid-map pandapower pickle contains investigative edits for transformer-header lines `841-848` that are documented but not yet validated as final network parameters.

## Current Project Phase
Heterogeneous Modbus dispatch hardening with voltage-regulation rollout, alongside SoC continuity support, schedule observability work, and grid-map digital-twin audit follow-up.
