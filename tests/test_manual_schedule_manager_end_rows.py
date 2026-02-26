import unittest

import pandas as pd

import manual_schedule_manager as msm


class ManualScheduleManagerEndRowsTests(unittest.TestCase):
    def test_numeric_rows_still_parse_without_end(self):
        rows = [
            {"hours": 0, "minutes": 0, "seconds": 0, "setpoint": 1.0},
            {"hours": 0, "minutes": 15, "seconds": 0, "setpoint": 2.0},
        ]
        start = pd.Timestamp("2026-02-26T10:00:00+01:00")
        series_df, end_time = msm.manual_editor_rows_to_series_and_end_time(rows, start, timezone_name="Europe/Madrid")
        self.assertEqual(len(series_df), 3)
        self.assertEqual(float(series_df.iloc[-1]["setpoint"]), float(series_df.iloc[-2]["setpoint"]))
        self.assertIsNone(end_time)

    def test_csv_round_trip_supports_end_token(self):
        rows = [
            {"hours": 0, "minutes": 0, "seconds": 0, "setpoint": 1.0, "kind": "value"},
            {"hours": 0, "minutes": 10, "seconds": 0, "setpoint": 2.0, "kind": "value"},
            {"hours": 0, "minutes": 30, "seconds": 0, "setpoint": None, "kind": "end"},
        ]
        csv_text = msm.manual_editor_rows_to_relative_csv_text(rows)
        self.assertIn("end", csv_text.lower())
        loaded = msm.load_manual_editor_rows_from_relative_csv_text(csv_text)
        self.assertEqual(loaded[-1]["kind"], "end")
        self.assertIsNone(loaded[-1]["setpoint"])

    def test_rejects_end_row_not_last(self):
        rows = [
            {"hours": 0, "minutes": 0, "seconds": 0, "setpoint": 1.0},
            {"hours": 0, "minutes": 5, "seconds": 0, "setpoint": "end"},
            {"hours": 0, "minutes": 10, "seconds": 0, "setpoint": 2.0},
        ]
        with self.assertRaisesRegex(ValueError, "end row must be the last row"):
            msm.manual_editor_rows_to_relative_csv_text(rows)

    def test_end_row_not_strictly_after_previous_is_auto_pushed_forward(self):
        rows = [
            {"hours": 0, "minutes": 0, "seconds": 0, "setpoint": 1.0},
            {"hours": 0, "minutes": 0, "seconds": 0, "setpoint": None, "kind": "end"},
        ]
        normalized = msm.load_manual_editor_rows_from_relative_csv_text(
            msm.manual_editor_rows_to_relative_csv_text(rows)
        )
        self.assertEqual(normalized[-1]["kind"], "end")
        self.assertEqual((normalized[-1]["hours"], normalized[-1]["minutes"], normalized[-1]["seconds"]), (0, 1, 0))

    def test_rejects_non_numeric_non_end_setpoint(self):
        rows = [
            {"hours": 0, "minutes": 0, "seconds": 0, "setpoint": "abc"},
        ]
        with self.assertRaisesRegex(ValueError, "setpoint must be numeric"):
            msm.manual_editor_rows_to_relative_csv_text(rows)

    def test_round_trip_series_to_editor_rows_and_back_preserves_terminal_duplicate(self):
        start = pd.Timestamp("2026-02-26T10:00:00+01:00")
        series_df = pd.DataFrame(
            {"setpoint": [1.0, 2.0, 2.0]},
            index=pd.DatetimeIndex([start, start + pd.Timedelta(minutes=15), start + pd.Timedelta(minutes=30)]),
        )
        start_out, rows = msm.manual_series_and_end_time_to_editor_rows_and_start(
            series_df,
            timezone_name="Europe/Madrid",
        )
        self.assertEqual(pd.Timestamp(start_out), start)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[-1]["kind"], "end")
        roundtrip_df, roundtrip_end = msm.manual_editor_rows_to_series_and_end_time(
            rows,
            start_out,
            timezone_name="Europe/Madrid",
        )
        self.assertTrue(
            msm.normalize_manual_series_df(roundtrip_df, "Europe/Madrid").equals(
                msm.normalize_manual_series_df(series_df, "Europe/Madrid")
            )
        )
        self.assertIsNone(roundtrip_end)

    def test_csv_without_end_row_appends_terminal_end_row(self):
        csv_text = "hours,minutes,seconds,setpoint\n0,0,0,1\n0,10,0,2\n"
        rows = msm.load_manual_editor_rows_from_relative_csv_text(csv_text)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[-1]["kind"], "end")
        self.assertEqual((rows[-1]["hours"], rows[-1]["minutes"], rows[-1]["seconds"]), (0, 11, 0))

    def test_csv_with_terminal_duplicate_last_row_is_displayed_as_end(self):
        csv_text = "hours,minutes,seconds,setpoint\n0,0,0,1\n0,10,0,2\n0,20,0,2\n"
        rows = msm.load_manual_editor_rows_from_relative_csv_text(csv_text)
        self.assertEqual(rows[-1]["kind"], "end")
        self.assertIsNone(rows[-1]["setpoint"])


if __name__ == "__main__":
    unittest.main()
