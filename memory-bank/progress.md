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
- logs tab with live `Today` (current date file tail) and selectable historical files,
- branded UI theme (tokenized CSS, local font assets, class-based styling hooks, and contrast-tuned controls).
6. Automated validation now includes:
- module compile checks (`python3 -m py_compile *.py`),
- unit/smoke regression suite (`python -m unittest discover -s tests -v`),
- CI execution via `.github/workflows/ci.yml`.

## In Progress
1. Memory-bank reconciliation to reflect completed Stage A/B/C/D implementation status.
2. Follow-up reliability design for dashboard callback de-blocking (replace synchronous Modbus reads with cached plant state).
3. Remote transport smoke coverage design (repeatable unattended checks).

## Next
1. Add targeted tests for safe-stop result handling and source/transport switch flows.
2. Add repeatable remote transport smoke checks.
3. Define and implement log retention/cleanup policy.
4. Add lightweight dashboard visual regression/smoke checklist.
5. Expand README operator runbook/troubleshooting sections.

## Known Issues / Gaps
1. No persistent store for API posting retry queue across process restarts.
2. Dashboard status callback still performs direct Modbus polling and can block under slow endpoints.
3. Operational runbook and incident handling guidance are still thin.
4. UI styling changes are still validated manually; no screenshot/DOM snapshot checks in CI.
5. `schedule_manager.py` remains in repository for legacy compatibility only and is intentionally deprecated.

## Current Project Phase
Runtime architecture is stable for dual-plant operation; current priority is reliability hardening of remaining high-risk paths and operational docs.
