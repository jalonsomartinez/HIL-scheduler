import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from dashboard.agent import format_mfrr_polling_status_line


class DashboardApiTabStatusTests(unittest.TestCase):
    def test_format_mfrr_polling_status_line_includes_core_fields(self):
        tz = ZoneInfo("Europe/Madrid")

        def format_ts(value):
            if value is None:
                return None
            return value.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S %Z")

        now_value = datetime(2026, 3, 13, 11, 0, tzinfo=tz)
        next_value = datetime(2026, 3, 13, 11, 1, tzinfo=tz)
        line = format_mfrr_polling_status_line(
            {
                "last_attempt_at": now_value,
                "last_result": "ok",
                "next_scheduled_at": next_value,
                "last_points_lib": 4,
            },
            format_ts=format_ts,
        )

        self.assertIn("mFRR Polling: Last=", line)
        self.assertIn("| Result=ok |", line)
        self.assertIn("| Next=", line)
        self.assertIn("| LIB points=4", line)

    def test_format_mfrr_polling_status_line_appends_error_when_present(self):
        line = format_mfrr_polling_status_line(
            {
                "last_result": "error",
                "last_error": "request timeout",
            },
            format_ts=lambda _value: None,
        )

        self.assertIn("Last=never", line)
        self.assertIn("Result=error", line)
        self.assertIn("Next=n/a", line)
        self.assertIn("Error=request timeout", line)


if __name__ == "__main__":
    unittest.main()
