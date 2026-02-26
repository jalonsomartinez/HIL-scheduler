import unittest
from zoneinfo import ZoneInfo

import pandas as pd

from scheduling.runtime import build_effective_schedule_frame


class ScheduleRuntimeEndTimeTests(unittest.TestCase):
    def test_manual_terminal_row_stops_override_and_api_resumes(self):
        tz = ZoneInfo("Europe/Madrid")
        base = pd.Timestamp("2026-02-26T10:00:00+01:00")
        api_df = pd.DataFrame(
            {
                "power_setpoint_kw": [100.0, 100.0],
                "reactive_power_setpoint_kvar": [10.0, 10.0],
            },
            index=pd.DatetimeIndex([base, base + pd.Timedelta(hours=1)]),
        )
        end_time = base + pd.Timedelta(minutes=30)
        manual_p_df = pd.DataFrame({"setpoint": [200.0, 200.0]}, index=pd.DatetimeIndex([base, end_time]))

        effective = build_effective_schedule_frame(
            api_df,
            manual_p_df,
            pd.DataFrame(columns=["setpoint"]),
            manual_p_enabled=True,
            manual_q_enabled=False,
            tz=tz,
        )

        self.assertAlmostEqual(float(effective.loc[base, "power_setpoint_kw"]), 200.0)
        self.assertIn(end_time, effective.index)
        self.assertAlmostEqual(float(effective.loc[end_time, "power_setpoint_kw"]), 100.0)
        self.assertAlmostEqual(float(effective.loc[end_time, "reactive_power_setpoint_kvar"]), 10.0)

    def test_p_and_q_end_times_apply_independently(self):
        tz = ZoneInfo("Europe/Madrid")
        base = pd.Timestamp("2026-02-26T10:00:00+01:00")
        api_df = pd.DataFrame(
            {
                "power_setpoint_kw": [100.0, 100.0],
                "reactive_power_setpoint_kvar": [10.0, 10.0],
            },
            index=pd.DatetimeIndex([base, base + pd.Timedelta(hours=1)]),
        )
        p_end = base + pd.Timedelta(minutes=15)
        q_end = base + pd.Timedelta(minutes=45)
        manual_p_df = pd.DataFrame({"setpoint": [200.0, 200.0]}, index=pd.DatetimeIndex([base, p_end]))
        manual_q_df = pd.DataFrame({"setpoint": [50.0, 50.0]}, index=pd.DatetimeIndex([base, q_end]))

        effective = build_effective_schedule_frame(
            api_df,
            manual_p_df,
            manual_q_df,
            manual_p_enabled=True,
            manual_q_enabled=True,
            tz=tz,
        )

        self.assertAlmostEqual(float(effective.loc[base, "power_setpoint_kw"]), 200.0)
        self.assertAlmostEqual(float(effective.loc[base, "reactive_power_setpoint_kvar"]), 50.0)
        self.assertAlmostEqual(float(effective.loc[p_end, "power_setpoint_kw"]), 100.0)
        self.assertAlmostEqual(float(effective.loc[p_end, "reactive_power_setpoint_kvar"]), 50.0)
        self.assertAlmostEqual(float(effective.loc[q_end, "reactive_power_setpoint_kvar"]), 10.0)


if __name__ == "__main__":
    unittest.main()
