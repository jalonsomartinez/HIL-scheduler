# Memory-Bank Drift Report and Update Plan

Date: 2026-02-21
Scope checked: `memory-bank/projectbrief.md`, `memory-bank/productContext.md`, `memory-bank/systemPatterns.md`, `memory-bank/techContext.md`, `memory-bank/activeContext.md`, `memory-bank/progress.md`, `memory-bank/roadmap.md`.

## 1. Drift Report

## Status summary
- Major structure and section templates are compliant.
- Word budgets are within target ranges.
- Most runtime-contract statements are still aligned with code.

## Confirmed drift or ambiguity

1. Lock-discipline statement is stronger than current implementation reality.
- Memory reference: `memory-bank/systemPatterns.md` (Locking Discipline section).
- Code evidence: `measurement_agent.py:222`, `measurement_agent.py:231`, `measurement_agent.py:275` perform dataframe concat/write under lock.
- Drift type: contract text currently reads as strict current behavior, but code partially violates it.

2. Config schema section does not distinguish active vs legacy/inactive runtime usage.
- Memory reference: `memory-bank/techContext.md` (Configuration Schema section).
- Code evidence: `config_loader.py:346`, `config_loader.py:377` parse schedule/compression keys; active runtime modules do not consume these keys for behavior (`dashboard_agent.py`, `scheduler_agent.py`, `measurement_agent.py` do not use them).
- Drift type: ambiguity; readers may assume these keys influence current runtime.

3. Dashboard styling statement is slightly overstated.
- Memory reference: `memory-bank/techContext.md` (Dashboard Styling Conventions).
- Code evidence: inline style dictionaries still present in log/posting renderers (`dashboard_agent.py:370`, `dashboard_agent.py:396`, `dashboard_agent.py:1383`).
- Drift type: partial mismatch between “class-driven” intent and actual implementation.

4. Active tracking files should include latest audit-driven risks.
- Memory references: `memory-bank/activeContext.md`, `memory-bank/progress.md`, `memory-bank/roadmap.md`.
- Drift type: omission; the new audit findings (orphan module, duplicated stale-lookup logic, synchronous Modbus UI polling risk) are not yet captured.

## 2. Memory Update Plan (Execution Order)

## Phase 1 (first): refresh `activeContext.md`, `progress.md`, `roadmap.md`

### `memory-bank/activeContext.md`
Apply the following updates:
- In `Current Focus (Now)`, add:
  - "Execute staged refactor for shared Modbus/schedule helpers and concern separation in dashboard/measurement agents."
- In `Open Decisions and Risks`, add:
  - "Retire vs retain `schedule_manager.py` legacy path."
  - "Define whether `recording.compression.*` remains config-only or becomes active runtime behavior."
  - "Remove synchronous Modbus polling from Dash callbacks by introducing cached plant-state publication."
- In rolling log for 2026-02-21, add:
  - "Completed full code audit and memory drift audit; generated staged A-D cleanup roadmap."

### `memory-bank/progress.md`
Apply the following updates:
- In `In Progress`, add:
  - "Stage A extraction design: shared endpoint resolver + shared setpoint/stale helper."
- In `Known Issues / Gaps`, add:
  - "`schedule_manager.py` is not on active runtime path and can drift."
  - "Dashboard interval callbacks perform direct Modbus polling and may degrade responsiveness on slow endpoints."
  - "Schedule and compression config keys are parsed but partially inactive in current runtime behavior."

### `memory-bank/roadmap.md`
Apply the following updates in `P0`/`P1` priorities:
- Add near-term item:
  - "Extract shared schedule stale-lookup helper and enforce single-owner logic for scheduler/dashboard."
- Add near-term item:
  - "Extract shared Modbus endpoint/register resolver and remove module-local duplicates."
- Add operational hardening item:
  - "Move dashboard Modbus polling to agent-cached state and keep callbacks read-only."
- Add cleanup item:
  - "Deprecate/remove orphan `schedule_manager.py` after compatibility check."

## Phase 2: reconcile supporting active files

### `memory-bank/systemPatterns.md`
- In `Locking Discipline`, change wording from strict current-state claim to:
  - target contract + note that measurement cache paths still contain lock-scoped dataframe operations scheduled for refactor.

### `memory-bank/techContext.md`
- In `Configuration Schema`, annotate `schedule.*` and `recording.compression.*` as currently parsed with limited/no active runtime effect in current agent flow.
- In `Dashboard Styling Conventions`, reword to "primarily class-driven" and note remaining inline renderer styles.

### `memory-bank/projectbrief.md` and `memory-bank/productContext.md`
- No mandatory corrections required from this audit pass.

## 3. Archive/Retention Actions

- No archive move is required now; all active logs are within the rolling 30-day window.
- Re-evaluate archive compaction when audit/remediation narrative exceeds active file budget.

## 4. Verification Checklist for Memory Update PR

- All seven active files retain required section headers.
- `activeContext.md`, `progress.md`, `roadmap.md` updated first.
- Drift notes in `systemPatterns.md` and `techContext.md` reflect current code truth.
- No historical narrative older than 30 days introduced into active memory files.

## 5. Reconciliation Status (Updated 2026-02-21)

Implemented:
- `memory-bank/activeContext.md` refreshed with Stage A/B/C/D progress and current risks.
- `memory-bank/progress.md` refreshed to reflect active regression suite + CI status.
- `memory-bank/roadmap.md` reprioritized around remaining safety/operational gaps.
- `memory-bank/systemPatterns.md` locking discipline wording corrected to target + current exception.
- `memory-bank/techContext.md` updated to clarify parsed-vs-active config keys and legacy alias gating behavior.
- Follow-up refresh captured new runtime-control extraction (`dashboard_control.py`) and shared-state contract constructor (`build_initial_shared_data`).

Result:
- Active memory now reflects current runtime and cleanup status without requiring archive moves.
