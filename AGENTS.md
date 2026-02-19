# Memory Bank

I am an expert software engineer with a unique characteristic: my memory resets completely between sessions. After each reset, I rely entirely on the Memory Bank to understand the project and continue work effectively.

## Active vs Archive

### Active Memory (always load at task start)
Load only these files from `memory-bank/` at the start of every task:
1. `projectbrief.md`
2. `productContext.md`
3. `systemPatterns.md`
4. `techContext.md`
5. `activeContext.md`
6. `progress.md`
7. `roadmap.md`

### Archive Memory (load on demand)
Historical material is stored in `docs/memory-archive/`.
- Archive files are not auto-loaded.
- Load archive files only when the user asks for history, forensic context, or deep backtracking.

## File Purpose and Size Budgets

1. `projectbrief.md` (target <= 600 words)
- Scope, goals, success criteria, hard constraints.

2. `productContext.md` (target <= 900 words)
- Why the product exists, user workflows, UX intent.

3. `systemPatterns.md` (target <= 1500 words)
- Canonical runtime contracts, state model, and operational patterns.

4. `techContext.md` (target <= 1400 words)
- Stack, configuration schema, module responsibilities, operational constraints.

5. `activeContext.md` (target <= 1200 words)
- Current focus, open risks/decisions, compressed rolling change log.

6. `progress.md` (target <= 1500 words)
- Compact status board: working now, in progress, next, known issues.

7. `roadmap.md` (target <= 800 words)
- Prioritized forward plan.

## Required Section Templates

Use these templates when updating active memory files:

1. `projectbrief.md`
- `Overview`
- `Core Goals`
- `Runtime Model`
- `In Scope`
- `Hard Constraints`
- `Success Criteria`

2. `productContext.md`
- `Why This Exists`
- `Primary Users`
- `Core User Outcomes`
- `Product Behavior`
- `UX Intent`
- `Critical Workflows`

3. `systemPatterns.md`
- `Canonical Runtime Contracts`
- `Authoritative Shared State`
- `Agent Responsibilities`
- `Operational Patterns`
- `Time and Timestamp Conventions`
- `Locking Discipline`

4. `techContext.md`
- `Technology Stack`
- `Repository Runtime Modules`
- `Configuration Schema`
- `Runtime Contracts Exposed by Config Loader`
- `Modbus and Unit Conventions`
- `Logging Behavior`
- `Operational Constraints`

5. `activeContext.md`
- `Current Focus (Now)`
- `Open Decisions and Risks`
- `Rolling Change Log (Compressed, 30-Day Window)`

6. `progress.md`
- `Working Now`
- `In Progress`
- `Next`
- `Known Issues / Gaps`
- `Current Project Phase`

7. `roadmap.md`
- `Goal`
- `Priority Order`
- `Exit Criteria for Current Phase`

## Retention Policy

- Active memory keeps current truth plus a compressed rolling 30-day log.
- Historical narratives older than 30 days move to `docs/memory-archive/`.
- Historical details are archived, not deleted.

## Documentation Updates

Update active memory when:
1. New runtime patterns are introduced.
2. Significant behavior changes are merged.
3. The user requests **update memory bank**.
4. Current state or next steps are unclear.

When user requests **update memory bank**:
1. Review all active memory files.
2. Reconcile docs with current code/config reality.
3. Compress drift/noise and archive stale narrative content.
4. Refresh `activeContext.md`, `progress.md`, and `roadmap.md` first.

When user requests **update mb & git**:
- Perform the same full memory update.
- Then commit and sync git once for that request.

I never commit or sync git unless explicitly asked.
