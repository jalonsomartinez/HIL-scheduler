# Progress: HIL Scheduler

## Working Now
1. Dual logical plants (`lib`, `vrfb`) run under a shared global source/transport model with per-plant dispatch and recording gates.
2. Scheduler dispatches per plant from manual or API maps and applies API stale-setpoint guardrails.
3. Local emulation runs both plant Modbus servers concurrently with SoC and power-limit behavior.
4. Measurement pipeline provides:
- anchored sampling timing,
- per-plant daily recording,
- in-memory plot cache,
- API measurement posting with retry/backoff, per-plant telemetry, and token re-auth retry on `401`/`403`.
5. Dashboard provides:
- per-plant Start/Stop + Record/Stop controls,
- global source/transport switching with confirmation and safe-stop,
- API status and posting health,
- logs tab with live and historical sources,
- branded UI theme (tokenized CSS, local font assets, class-based styling hooks, and contrast-tuned controls).

## In Progress
1. Documentation right-sizing and contract cleanup (active memory + archive split).
2. Converting operational knowledge into concise, low-drift memory-bank artifacts.
3. UI validation pass for branded dashboard readability across desktop/mobile control workflows.

## Next
1. Add automated tests for:
- per-plant dispatch gating,
- safe-stop result handling,
- recording boundary insertion and midnight rollover,
- API posting queue retry/overflow behavior,
- API auth-retry handling for `401`/`403` in fetch and post paths.
2. Add repeatable smoke checks for local and remote transport workflows.
3. Add a lightweight dashboard visual regression/smoke checklist to catch class/style regressions.
4. Define and implement logging retention/cleanup policy.
5. Expand README with architecture and operator runbook details.

## Known Issues / Gaps
1. No persistent store for API posting retry queue across process restarts.
2. No comprehensive CI test suite yet for dashboard callback regressions.
3. Operational runbook and incident handling guidance are still thin.
4. UI styling changes are still validated manually; no screenshot/DOM snapshot checks in CI.

## Current Project Phase
Runtime architecture is functional and feature-complete for dual-plant operation; current priority is reliability hardening (tests, validation, and operational docs).
