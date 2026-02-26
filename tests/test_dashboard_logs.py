import os
import tempfile
import unittest
from collections import deque
from contextlib import chdir
from datetime import datetime
from zoneinfo import ZoneInfo

from dash import html

from dashboard.logs import get_logs_dir, get_today_log_file_path, parse_and_format_historical_logs, read_log_tail


class DashboardLogsTests(unittest.TestCase):
    def test_parse_and_format_historical_logs_parses_valid_lines(self):
        content = "\n".join(
            [
                "2026-02-21 14:04:51 - INFO - Dashboard: record requested for LIB -> data/20260221_lib.csv",
                "2026-02-21 14:04:52 - WARNING - Scheduler: could not connect to LIB plant endpoint.",
                "2026-02-21 14:04:53 - ERROR - Measurement: failed writing data/20260221_lib.csv: boom",
                "not a log line",
            ]
        )

        formatted = parse_and_format_historical_logs(content)

        self.assertEqual(len(formatted), 3)
        self.assertIsInstance(formatted[0], html.Div)
        self.assertEqual(formatted[0].children[1].children, "INFO: ")
        self.assertEqual(formatted[1].children[1].children, "WARNING: ")
        self.assertEqual(formatted[2].children[1].children, "ERROR: ")

    def test_get_today_log_file_path_uses_timezone_date(self):
        tz = ZoneInfo("Europe/Madrid")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = get_today_log_file_path(tmpdir, tz)

            self.assertTrue(path.endswith("_hil_scheduler.log"))
            self.assertIn(datetime.now(tz).strftime("%Y-%m-%d"), os.path.basename(path))

    def test_get_logs_dir_accepts_dashboard_package_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "assets"), exist_ok=True)
            dashboard_dir = os.path.join(tmpdir, "dashboard")
            os.makedirs(dashboard_dir, exist_ok=True)

            self.assertEqual(get_logs_dir(dashboard_dir), os.path.join(tmpdir, "logs"))

    def test_read_log_tail_returns_last_lines(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with chdir(tmpdir):
                os.makedirs("logs", exist_ok=True)
                file_path = os.path.join("logs", "sample.log")
                with open(file_path, "w", encoding="utf-8") as handle:
                    for idx in range(1, 11):
                        handle.write(f"line-{idx}\n")

                tail = read_log_tail(file_path, max_lines=3)
                self.assertEqual(tail, "".join(deque([f"line-{i}\n" for i in range(1, 11)], maxlen=3)))


if __name__ == "__main__":
    unittest.main()
