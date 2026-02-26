import os
import tempfile
import unittest
from contextlib import chdir
from zoneinfo import ZoneInfo

import pandas as pd

from measurement.storage import MEASUREMENT_COLUMNS, find_latest_persisted_soc_for_plant


def _row(ts, soc_pu, p_kw=0.0):
    return {
        "timestamp": ts,
        "p_setpoint_kw": p_kw,
        "battery_active_power_kw": p_kw,
        "q_setpoint_kvar": 0.0,
        "battery_reactive_power_kvar": 0.0,
        "soc_pu": soc_pu,
        "p_poi_kw": p_kw,
        "q_poi_kvar": 0.0,
        "v_poi_kV": 20.0,
    }


class MeasurementStorageLatestSocTests(unittest.TestCase):
    def test_returns_latest_non_null_soc_across_daily_files(self):
        tz = ZoneInfo("Europe/Madrid")
        with tempfile.TemporaryDirectory() as tmpdir:
            with chdir(tmpdir):
                os.makedirs("data", exist_ok=True)
                pd.DataFrame(
                    [
                        _row("2026-02-20T10:00:00+01:00", 0.31),
                        _row("2026-02-20T12:00:00+01:00", 0.33),
                    ],
                    columns=MEASUREMENT_COLUMNS,
                ).to_csv("data/20260220_lib.csv", index=False)
                pd.DataFrame(
                    [
                        _row("2026-02-21T08:00:00+01:00", 0.41),
                        _row("2026-02-21T09:00:00+01:00", 0.42),
                    ],
                    columns=MEASUREMENT_COLUMNS,
                ).to_csv("data/20260221_lib.csv", index=False)

                result = find_latest_persisted_soc_for_plant("data", "LIB", "lib", tz)

                self.assertIsNotNone(result)
                self.assertAlmostEqual(result["soc_pu"], 0.42, places=6)
                self.assertTrue(str(result["file_path"]).endswith("data/20260221_lib.csv"))

    def test_ignores_null_boundary_rows_and_malformed_files(self):
        tz = ZoneInfo("Europe/Madrid")
        with tempfile.TemporaryDirectory() as tmpdir:
            with chdir(tmpdir):
                os.makedirs("data", exist_ok=True)
                with open("data/20260223_lib.csv", "w", encoding="utf-8") as handle:
                    handle.write("not,csv,measurement\nbad")
                pd.DataFrame(
                    [
                        _row("2026-02-22T10:00:00+01:00", 0.61),
                        _row("2026-02-22T10:05:00+01:00", float("nan")),
                    ],
                    columns=MEASUREMENT_COLUMNS,
                ).to_csv("data/20260222_lib.csv", index=False)

                result = find_latest_persisted_soc_for_plant("data", "lib", "lib", tz)

                self.assertIsNotNone(result)
                self.assertAlmostEqual(result["soc_pu"], 0.61, places=6)
                self.assertTrue(str(result["file_path"]).endswith("data/20260222_lib.csv"))

    def test_filters_by_plant_filename_suffix(self):
        tz = ZoneInfo("Europe/Madrid")
        with tempfile.TemporaryDirectory() as tmpdir:
            with chdir(tmpdir):
                os.makedirs("data", exist_ok=True)
                pd.DataFrame([_row("2026-02-24T10:00:00+01:00", 0.11)], columns=MEASUREMENT_COLUMNS).to_csv(
                    "data/20260224_lib.csv",
                    index=False,
                )
                pd.DataFrame([_row("2026-02-24T11:00:00+01:00", 0.91)], columns=MEASUREMENT_COLUMNS).to_csv(
                    "data/20260224_vrfb.csv",
                    index=False,
                )

                lib_result = find_latest_persisted_soc_for_plant("data", "lib", "lib", tz)
                vrfb_result = find_latest_persisted_soc_for_plant("data", "vrfb", "vrfb", tz)

                self.assertIsNotNone(lib_result)
                self.assertIsNotNone(vrfb_result)
                self.assertAlmostEqual(lib_result["soc_pu"], 0.11, places=6)
                self.assertAlmostEqual(vrfb_result["soc_pu"], 0.91, places=6)

    def test_clamps_out_of_range_soc_values(self):
        tz = ZoneInfo("Europe/Madrid")
        with tempfile.TemporaryDirectory() as tmpdir:
            with chdir(tmpdir):
                os.makedirs("data", exist_ok=True)
                pd.DataFrame([_row("2026-02-24T12:00:00+01:00", 1.7)], columns=MEASUREMENT_COLUMNS).to_csv(
                    "data/20260224_lib.csv",
                    index=False,
                )

                result = find_latest_persisted_soc_for_plant("data", "lib", "lib", tz)

                self.assertIsNotNone(result)
                self.assertEqual(result["soc_pu"], 1.0)


if __name__ == "__main__":
    unittest.main()
