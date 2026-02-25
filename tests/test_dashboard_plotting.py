import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

try:
    import pandas as pd

    from dashboard_plotting import DEFAULT_PLOT_THEME, DEFAULT_TRACE_COLORS, create_plant_figure
    _IMPORT_ERROR = None
except ModuleNotFoundError as exc:  # pragma: no cover - environment-dependent test skip
    pd = None
    _IMPORT_ERROR = exc


def _schedule_df(*timestamps):
    rows = []
    for idx, ts in enumerate(timestamps, start=1):
        rows.append(
            {
                "datetime": ts,
                "power_setpoint_kw": float(idx),
                "reactive_power_setpoint_kvar": float(idx) * 10.0,
            }
        )
    return pd.DataFrame(rows).set_index("datetime").sort_index()


def _measurements_df(*timestamps):
    rows = []
    for idx, ts in enumerate(timestamps, start=1):
        rows.append(
            {
                "timestamp": ts,
                "p_poi_kw": float(idx),
                "battery_active_power_kw": float(idx) + 0.1,
                "soc_pu": 0.5,
                "q_poi_kvar": float(idx) + 0.2,
                "battery_reactive_power_kvar": float(idx) + 0.3,
            }
        )
    return pd.DataFrame(rows)


def _trace_by_suffix(fig, suffix):
    for trace in fig.data:
        if str(trace.name).endswith(suffix):
            return trace
    raise AssertionError(f"Trace ending with {suffix!r} not found.")


def _x_as_timestamps(trace, tz=None):
    values = []
    for value in list(trace.x):
        ts = pd.Timestamp(value)
        if tz is not None:
            if ts.tzinfo is None:
                ts = ts.tz_localize(tz)
            else:
                ts = ts.tz_convert(tz)
        values.append(ts)
    return values


@unittest.skipIf(pd is None, f"plot/pandas unavailable: {_IMPORT_ERROR}")
class DashboardPlottingTests(unittest.TestCase):
    def setUp(self):
        self.tz = ZoneInfo("Europe/Madrid")
        self.plot_theme = dict(DEFAULT_PLOT_THEME)
        self.trace_colors = dict(DEFAULT_TRACE_COLORS)

    def _fig(self, schedule_df, measurements_df, **kwargs):
        return create_plant_figure(
            "lib",
            lambda plant_id: plant_id.upper(),
            schedule_df,
            measurements_df,
            uirevision_key="test",
            tz=self.tz,
            plot_theme=self.plot_theme,
            trace_colors=self.trace_colors,
            **kwargs,
        )

    def test_create_plant_figure_without_window_preserves_all_points(self):
        base = datetime(2026, 2, 23, 0, 0, tzinfo=self.tz)
        schedule_df = _schedule_df(base, base + timedelta(hours=1), base + timedelta(hours=2))
        measurements_df = _measurements_df(base, base + timedelta(hours=1), base + timedelta(hours=2))

        fig = self._fig(schedule_df, measurements_df)

        p_setpoint = _trace_by_suffix(fig, "P Setpoint")
        p_poi = _trace_by_suffix(fig, "P POI")
        self.assertEqual(len(p_setpoint.x), 3)
        self.assertEqual(len(p_poi.x), 3)

    def test_schedule_traces_are_cropped_to_window(self):
        window_start = datetime(2026, 2, 24, 0, 0, tzinfo=self.tz)
        window_end = window_start + timedelta(days=2)
        schedule_df = _schedule_df(
            window_start - timedelta(minutes=15),
            window_start,
            window_start + timedelta(days=1, minutes=15),
            window_end,
        )

        fig = self._fig(schedule_df, pd.DataFrame(), x_window_start=window_start, x_window_end=window_end)

        p_setpoint = _trace_by_suffix(fig, "P Setpoint")
        xs = _x_as_timestamps(p_setpoint, self.tz)
        self.assertEqual(xs, [pd.Timestamp(window_start), pd.Timestamp(window_start + timedelta(days=1, minutes=15))])

    def test_measurement_traces_are_cropped_after_timestamp_normalization(self):
        window_start = datetime(2026, 2, 24, 0, 0, tzinfo=self.tz)
        window_end = window_start + timedelta(days=2)
        measurements_df = _measurements_df(
            "2026-02-23T22:59:00+00:00",  # 23:59 local (excluded)
            "2026-02-23T23:00:00+00:00",  # 00:00 local (included)
            "2026-02-24T12:00:00+00:00",  # in-window
            "2026-02-25T23:00:00+00:00",  # end bound local (excluded)
        )

        fig = self._fig(pd.DataFrame(), measurements_df, x_window_start=window_start, x_window_end=window_end)

        p_poi = _trace_by_suffix(fig, "P POI")
        xs = _x_as_timestamps(p_poi, self.tz)
        self.assertEqual(len(xs), 2)
        self.assertEqual(xs[0], pd.Timestamp(window_start))
        self.assertTrue(xs[1] < pd.Timestamp(window_end))

    def test_window_boundary_is_start_inclusive_and_end_exclusive(self):
        window_start = datetime(2026, 2, 24, 0, 0, tzinfo=self.tz)
        window_end = window_start + timedelta(days=2)
        schedule_df = _schedule_df(window_start, window_end)
        measurements_df = _measurements_df(window_start.isoformat(), window_end.isoformat())

        fig = self._fig(schedule_df, measurements_df, x_window_start=window_start, x_window_end=window_end)

        p_setpoint = _trace_by_suffix(fig, "P Setpoint")
        p_poi = _trace_by_suffix(fig, "P POI")
        self.assertEqual(_x_as_timestamps(p_setpoint, self.tz), [pd.Timestamp(window_start)])
        self.assertEqual(_x_as_timestamps(p_poi, self.tz), [pd.Timestamp(window_start)])


if __name__ == "__main__":
    unittest.main()
