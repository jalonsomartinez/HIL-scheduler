import os
import tempfile
import unittest
from contextlib import chdir
from zoneinfo import ZoneInfo

import pandas as pd

from dashboard.history import (
    build_slider_marks,
    clamp_epoch_range,
    load_cropped_measurements_for_range,
    scan_measurement_history_index,
    serialize_measurements_for_download,
)
from measurement.storage import MEASUREMENT_COLUMNS


def _row(ts, p_kw):
    return {
        "timestamp": ts,
        "p_setpoint_kw": p_kw,
        "battery_active_power_kw": p_kw,
        "q_setpoint_kvar": 0.0,
        "battery_reactive_power_kvar": 0.0,
        "soc_pu": 0.5,
        "p_poi_kw": p_kw,
        "q_poi_kvar": 0.0,
        "v_poi_kV": 1.0,
    }


class DashboardHistoryTests(unittest.TestCase):
    def test_scan_index_maps_known_files_and_ignores_unknown(self):
        tz = ZoneInfo("Europe/Madrid")
        with tempfile.TemporaryDirectory() as tmpdir:
            with chdir(tmpdir):
                os.makedirs("data", exist_ok=True)
                pd.DataFrame(
                    [
                        _row("2026-02-21T13:10:03+01:00", 10.0),
                        _row("2026-02-21T13:11:03+01:00", 11.0),
                    ],
                    columns=MEASUREMENT_COLUMNS,
                ).to_csv("data/20260221_lib.csv", index=False)
                pd.DataFrame(
                    [
                        _row("2026-02-21T13:12:03+01:00", 20.0),
                    ],
                    columns=MEASUREMENT_COLUMNS,
                ).to_csv("data/20260221_vrfb.csv", index=False)
                pd.DataFrame([_row("2026-02-21T10:00:00+01:00", 1.0)], columns=MEASUREMENT_COLUMNS).to_csv(
                    "data/20260221_other.csv",
                    index=False,
                )
                with open("data/not_a_measurement.txt", "w", encoding="utf-8") as handle:
                    handle.write("x")

                index = scan_measurement_history_index("data", {"lib": "lib", "vrfb": "vrfb"}, tz)

                self.assertTrue(index["has_data"])
                self.assertEqual(len(index["files_by_plant"]["lib"]), 1)
                self.assertEqual(len(index["files_by_plant"]["vrfb"]), 1)
                self.assertEqual(index["files_by_plant"]["lib"][0]["rows"], 2)
                self.assertLess(index["global_start_ms"], index["global_end_ms"])

    def test_clamp_epoch_range_defaults_and_clamps(self):
        self.assertEqual(clamp_epoch_range(None, 100, 200), [100, 200])
        self.assertEqual(clamp_epoch_range([0, 1], 100, 200), [100, 200])
        self.assertEqual(clamp_epoch_range([90, 210], 100, 200), [100, 200])
        self.assertEqual(clamp_epoch_range([300, 400], 100, 200), [100, 200])
        self.assertEqual(clamp_epoch_range([150, 250], 100, 200), [150, 200])
        self.assertEqual(clamp_epoch_range([180, 120], 100, 200), [120, 180])
        self.assertEqual(clamp_epoch_range(["bad", 200], 100, 200), [100, 200])

    def test_load_cropped_measurements_is_inclusive(self):
        tz = ZoneInfo("Europe/Madrid")
        with tempfile.TemporaryDirectory() as tmpdir:
            with chdir(tmpdir):
                os.makedirs("data", exist_ok=True)
                df = pd.DataFrame(
                    [
                        _row("2026-02-21T13:10:00+01:00", 1.0),
                        _row("2026-02-21T13:11:00+01:00", 2.0),
                        _row("2026-02-21T13:12:00+01:00", 3.0),
                    ],
                    columns=MEASUREMENT_COLUMNS,
                )
                file_path = "data/20260221_lib.csv"
                df.to_csv(file_path, index=False)

                full_index = scan_measurement_history_index("data", {"lib": "lib", "vrfb": "vrfb"}, tz)
                file_meta = full_index["files_by_plant"]["lib"]

                ts_start = int(pd.Timestamp("2026-02-21T13:11:00+01:00").value // 1_000_000)
                ts_end = int(pd.Timestamp("2026-02-21T13:12:00+01:00").value // 1_000_000)
                cropped = load_cropped_measurements_for_range(file_meta, ts_start, ts_end, tz)

                self.assertEqual(len(cropped), 2)
                self.assertEqual(cropped.iloc[0]["p_poi_kw"], 2.0)
                self.assertEqual(cropped.iloc[1]["p_poi_kw"], 3.0)

    def test_serialize_measurements_for_download_keeps_column_order_and_iso(self):
        tz = ZoneInfo("Europe/Madrid")
        df = pd.DataFrame([_row("2026-02-21T13:10:03+01:00", 10.0)], columns=MEASUREMENT_COLUMNS)

        serialized = serialize_measurements_for_download(df, tz)

        self.assertEqual(list(serialized.columns), MEASUREMENT_COLUMNS)
        self.assertIsInstance(serialized.iloc[0]["timestamp"], str)
        self.assertIn("+01:00", serialized.iloc[0]["timestamp"])

    def test_build_slider_marks_returns_sparse_labels(self):
        tz = ZoneInfo("Europe/Madrid")
        marks = build_slider_marks(1_000, 11_000, tz, max_marks=5)
        self.assertGreaterEqual(len(marks), 2)
        self.assertLessEqual(len(marks), 5)


if __name__ == "__main__":
    unittest.main()
