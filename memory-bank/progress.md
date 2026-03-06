# Progress: HIL Scheduler

## Working Now
- Verifying shared Modbus transport behavior on real remote endpoints (LIB + VRFB, local + remote modes).
- Validating grouped-read measurement/observed paths against runtime stability and expected telemetry quality.
- Standardizing dependency versions across local and Tailscale-served servers to eliminate dashboard bundle drift.

## In Progress
1. Remote Modbus stabilization rollout:
   - shared per-endpoint client/session behavior in runtime,
   - contention checks on VRFB and LIB remote plants.
2. Static grouped-read rollout:
   - measurement fixed point-set grouped blocks,
   - observed-state grouped blocks including command readbacks.
3. Diagnostics-driven validation:
   - replaying `dashboard_like` vs `app_like_parallel` vs `app_like_serial` expectations from runbook outputs.
4. Regression alignment:
   - keeping unit coverage green for new transport/grouping helpers,
   - deciding whether to adapt/replace fake-client local smoke test that fails with pooled client semantics.
5. Deployment parity hardening:
   - pinned direct dependencies in `requirements.txt` to exact known-good versions,
   - rolling out pinned installs on each host that serves dashboards.

## Next
1. Validate pooled Modbus behavior in full end-to-end field runs (including long-duration remote operation).
2. Decide whether to group scheduler readback requests or keep scheduler logic as single-point reads for simplicity.
3. Update/replace failing local smoke test double setup to match pooled connection behavior.
4. Continue pending hardening work: API credentials UX/security and lightweight dashboard visual regression checks.
5. Verify both local and remote origins serve matching Dash component-suite versions after redeploy.

## Known Issues / Gaps
1. No persistent durability for measurement-post retry queue across process restarts.
2. Serialized command execution can delay subsequent commands during long safe-stop/transport sequences.
3. Manual schedule drafts are shared in server state (single-operator assumption).
4. UI visual regressions are currently caught mainly by manual review.
5. API password is process-memory only (env/bootstrap or dashboard save); no encrypted persistence layer.
6. `tests.test_local_runtime_smoke` currently fails with new pooled Modbus client semantics under fake client patching.
7. Cross-server UI consistency depends on each host reinstalling from pinned `requirements.txt`; mixed environments can still render controls differently.

## Current Project Phase
Remote Modbus reliability hardening, request-shaping optimization, and cross-server deployment parity.
