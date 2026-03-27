# Product Context: HIL Scheduler

## Why This Exists
Operators need one runtime that can safely execute multi-market battery schedules and show what the controller intends versus what the plant does.

## Primary Users
1. Operators on the private dashboard controlling plants and API behavior.
2. Engineers validating schedule composition, dispatch, and telemetry quality.
3. External viewers of the public read-only dashboard.

## Core User Outcomes
1. Run/stop plants and dispatch safely with explicit control feedback.
2. Trust that the active schedule reflects day-ahead + mFRR market intent.
3. Distinguish schedule intent (`Pref`, `day-ahead`, `mfrr`) from measured response.
4. Monitor API/mFRR polling health without noisy logs.
5. Record/export historical data with schedule-intent columns for analysis.

## Product Behavior
- API schedule intent is now three-layer:
  - `Pref` remains total setpoint used by dispatch,
  - `day-ahead` and `mfrr` are visible as separate traces.
- mFRR behavior:
  - LIB mFRR is fetched from API over near-term window,
  - VRFB uses explicit zero mFRR aligned to LIB mFRR timestamps.
- Private API tab shows operational polling telemetry:
  - last poll attempt time,
  - last result (`never|ok|error|disabled`),
  - next scheduled poll time,
  - last LIB point count and optional error text.
- Operator/public plots support historical fallback from recorded schedule columns:
  - `p_schedule_total_kw` (fallback legacy `p_setpoint_kw`),
  - `p_schedule_day_ahead_kw`,
  - `p_schedule_mfrr_kw`.
- API credential workflow remains split:
  - `Save Password` stores runtime password,
  - `Connect/Disconnect` controls runtime API state.
- Navigation model (private + public dashboards):
  - menu-driven route pages replace tabbed navigation,
  - private routes: `/status`, `/plots`, `/manual-schedule`, `/api-schedule`, `/logs`,
  - public routes: `/status`, `/plots`,
  - unknown routes fallback to status content (no 404 page).

## UX Intent
- Keep total schedule semantics stable for operators (`Pref` unchanged).
- Increase transparency by exposing market-component traces and poll health.
- Reduce noise in console operations while preserving actionable errors.

## Critical Workflows
1. mFRR polling loop: periodic fetch -> update per-plant mFRR maps -> recompute total schedule -> publish telemetry.
2. API monitoring: operator checks API tab first line (connection/posting) and second line (mFRR polling state).
3. Dispatch execution: scheduler continues consuming authoritative total schedule.
4. Historical review: users load CSV-backed data and compare total/day-ahead/mFRR intent against POI/battery response.
5. Section navigation: users switch views through left menu links and can deep-link to routes directly.
