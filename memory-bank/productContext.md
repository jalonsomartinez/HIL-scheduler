# Product Context: HIL Scheduler

## Why This Exists
Operators need one runtime that can safely execute multi-market battery schedules and show both controller intent and plant response, even when plant Modbus interfaces are inconsistent across deployments.

## Primary Users
1. Operators using the private dashboard to control plants, dispatching, recording, and API state.
2. Engineers validating schedule composition, Modbus dispatch behavior, and telemetry quality.
3. External viewers consuming the public read-only dashboard.

## Core User Outcomes
1. Run/stop plants and dispatch safely with explicit control feedback.
2. Trust that the active schedule reflects day-ahead + mFRR market intent.
3. Distinguish schedule intent (`Pref`, `day-ahead`, `mfrr`) from measured response.
4. Monitor API/mFRR polling health without noisy logs.
5. Keep dispatch usable across aggregate, per-phase, and trigger-latched devices.
6. Preserve a usable SoC signal even when hardware does not expose a direct Modbus `soc` point.

## Product Behavior
- API schedule intent remains three-layer:
  - `Pref` is the total setpoint used by dispatch,
  - `day-ahead` and `mfrr` remain visible as separate traces.
- Dispatch/write behavior:
  - scheduler and control paths choose aggregate or per-phase writes from endpoint schema,
  - per-phase mode splits total `P` and `Q` evenly across U/V/W setpoint points,
  - exact register readback is used to suppress redundant rewrites when target words already match,
  - optional `trigger` pulses are applied after successful writes for devices that require a latch/apply handshake.
- SoC behavior:
  - a real Modbus `soc` point remains authoritative when present,
  - startup seed resolves from latest persisted disk value or `STARTUP_INITIAL_SOC_PU`,
  - measurement runtime estimates SoC from battery active power when no real SoC is available.
- mFRR behavior:
  - LIB mFRR is fetched from API over the near-term window,
  - VRFB uses explicit zero mFRR aligned to LIB mFRR timestamps.
- Private API page shows operational polling telemetry:
  - last poll attempt,
  - last result (`never|ok|error|disabled`),
  - next scheduled poll,
  - last LIB point count and optional error text.
- Operator/public plots support recorded schedule columns:
  - `p_schedule_total_kw`,
  - `p_schedule_day_ahead_kw`,
  - `p_schedule_mfrr_kw`.
- API credential workflow stays split:
  - `Save Password` stores runtime password,
  - `Connect/Disconnect` controls runtime API state.
- Navigation remains menu-driven:
  - private routes: `/status`, `/plots`, `/manual-schedule`, `/api-schedule`, `/logs`,
  - public routes: `/status`, `/plots`.

## UX Intent
- Keep operator-facing schedule semantics stable (`Pref` still means the authoritative total setpoint).
- Hide Modbus endpoint quirks behind predictable control flows while still surfacing failures clearly.
- Increase transparency by exposing market-component traces, poll health, and dispatch-write outcomes.
- Reduce noise in runtime logs without hiding actionable errors.

## Critical Workflows
1. mFRR polling loop: fetch -> update per-plant mFRR maps -> recompute total schedule -> publish telemetry.
2. API monitoring: operator checks connection/posting state and mFRR poll state on the API page.
3. Dispatch execution: scheduler consumes authoritative total schedule, selects aggregate vs per-phase write plan, and pulses `trigger` when configured.
4. Local start flow: control engine resolves startup SoC seed, requests local emulator seeding, resets `trigger` to normal when needed, enables the plant, and writes initial setpoints.
5. Historical review: users compare total/day-ahead/mFRR intent against POI and battery response from CSV-backed history.
6. Section navigation: users switch views through the left menu and can deep-link directly to routes.
