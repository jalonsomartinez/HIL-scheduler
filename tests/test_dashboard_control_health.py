import unittest
from datetime import datetime, timedelta, timezone

from dashboard.control_health import summarize_plant_modbus_health


class DashboardControlHealthTests(unittest.TestCase):
    def test_modbus_link_uses_transport_health_not_last_observed_read_result(self):
        now_ts = datetime(2026, 4, 16, 6, 0, 0, tzinfo=timezone.utc)
        lines = summarize_plant_modbus_health(
            {
                "state": "down",
                "last_success_at": now_ts - timedelta(hours=2),
                "consecutive_failures": 4,
                "reconnect_count": 2,
                "last_reset_reason": "stale_threshold",
                "last_reset_at": now_ts - timedelta(minutes=5),
                "stale_reset_count": 1,
                "reset_after_stale_seconds": 15.0,
                "last_error": {"timestamp": now_ts, "message": "connect_failed"},
                "waiting_count": 0,
            },
            {
                "read_status": "ok",
                "last_success": now_ts - timedelta(hours=2),
                "stale": True,
                "consecutive_failures": 3,
                "last_error": {"timestamp": now_ts, "code": "read_error", "message": "No observed points available."},
                "enable_state": None,
                "start_command_state": None,
                "stop_command_state": None,
            },
            now_ts,
        )

        self.assertIn("Modbus link: DOWN", lines[0])
        self.assertIn("Last reset: stale_threshold", lines[2])
        self.assertIn("Last observed read result: OK", lines[3])
        self.assertIn("Link error", lines[5])


if __name__ == "__main__":
    unittest.main()
