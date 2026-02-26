import unittest
from datetime import datetime, timedelta, timezone

from dashboard_control_health import (
    format_age_seconds,
    summarize_control_engine_status,
    summarize_control_queue_status,
    summarize_dispatch_write_status,
    summarize_plant_modbus_health,
)


class DashboardControlHealthTests(unittest.TestCase):
    def test_format_age_seconds(self):
        now_ts = datetime(2026, 2, 25, 12, 0, 2, tzinfo=timezone.utc)
        self.assertEqual(
            format_age_seconds(datetime(2026, 2, 25, 12, 0, 1, tzinfo=timezone.utc), now_ts),
            "1.0s",
        )
        self.assertEqual(format_age_seconds(None, now_ts), "n/a")

    def test_summarize_control_engine_status_no_active_command(self):
        now_ts = datetime(2026, 2, 25, 12, 0, tzinfo=timezone.utc)
        text = summarize_control_engine_status(
            {
                "alive": True,
                "queue_depth": 0,
                "last_finished_command": {
                    "id": "cmd-000111",
                    "kind": "plant.start",
                    "state": "succeeded",
                    "finished_at": now_ts,
                    "message": None,
                },
            },
            now_ts,
        )
        self.assertIn("Control Engine: Alive", text)
        self.assertIn("Queue=0", text)
        self.assertIn("Active=None", text)
        self.assertIn("plant.start succeeded", text)

    def test_summarize_control_engine_status_active_and_exception(self):
        now_ts = datetime(2026, 2, 25, 12, 0, 5, tzinfo=timezone.utc)
        text = summarize_control_engine_status(
            {
                "alive": True,
                "queue_depth": 3,
                "active_command_id": "cmd-000123",
                "active_command_kind": "plant.stop",
                "active_command_started_at": now_ts - timedelta(seconds=4),
                "last_exception": {"timestamp": now_ts, "message": "unexpected loop error"},
            },
            now_ts,
        )
        self.assertIn("Active=plant.stop (cmd-000123, 4.0s)", text)
        self.assertIn("Loop error:", text)

    def test_summarize_control_queue_status_high_backlog(self):
        text = summarize_control_queue_status(
            {"queue_depth": 7, "queued_count": 6, "running_count": 1, "failed_recent_count": 2}
        )
        self.assertIn("queued=6", text)
        self.assertIn("running=1", text)
        self.assertIn("recent_failed=2", text)
        self.assertIn("Backlog: HIGH", text)

    def test_summarize_plant_modbus_health_ok_and_stale_error(self):
        now_ts = datetime(2026, 2, 25, 12, 0, 5, tzinfo=timezone.utc)
        ok_lines = summarize_plant_modbus_health(
            {
                "read_status": "ok",
                "stale": False,
                "last_success": now_ts - timedelta(milliseconds=800),
                "consecutive_failures": 0,
                "error": None,
                "last_error": None,
            },
            now_ts,
        )
        self.assertIn("Modbus link: OK", ok_lines[0])
        self.assertIn("Obs age: 0.8s", ok_lines[0])

        err_lines = summarize_plant_modbus_health(
            {
                "read_status": "connect_failed",
                "stale": True,
                "last_success": now_ts - timedelta(seconds=4),
                "consecutive_failures": 6,
                "error": "socket timeout",
                "last_error": {"code": "connect_failed", "message": "socket timeout", "timestamp": now_ts},
            },
            now_ts,
        )
        self.assertIn("CONNECT_FAILED", err_lines[0])
        self.assertIn("stale (4.0s)", err_lines[0])
        self.assertIn("Failures: 6", err_lines[0])
        self.assertEqual(len(err_lines), 2)
        self.assertIn("Error (CONNECT_FAILED): socket timeout", err_lines[1])

    def test_summarize_dispatch_write_status_formats_success_and_error(self):
        lines = summarize_dispatch_write_status(
            {
                "last_attempt_status": "ok",
                "last_attempt_at": datetime(2026, 2, 25, 12, 0, 5, tzinfo=timezone.utc),
                "last_attempt_p_kw": 12.3,
                "last_attempt_q_kvar": -4.5,
                "last_attempt_source": "scheduler",
                "last_error": None,
            },
            dispatch_enabled=True,
        )
        self.assertIn("Dispatch: Sending", lines[0])
        self.assertIn("Last write: OK", lines[0])
        self.assertIn("P=12.300 kW", lines[1])
        self.assertIn("Source: scheduler", lines[1])

        err_lines = summarize_dispatch_write_status(
            {"last_attempt_status": "failed", "last_error": "timeout"},
            dispatch_enabled=False,
        )
        self.assertIn("Dispatch: Paused", err_lines[0])
        self.assertIn("FAILED", err_lines[0])
        self.assertIn("Dispatch error: timeout", err_lines[-1])

    def test_summarize_dispatch_write_status_includes_scheduler_readback_telemetry(self):
        lines = summarize_dispatch_write_status(
            {
                "last_attempt_status": "partial",
                "last_attempt_at": datetime(2026, 2, 25, 12, 0, 5, tzinfo=timezone.utc),
                "last_attempt_p_kw": 12.3,
                "last_attempt_q_kvar": -4.5,
                "last_attempt_source": "scheduler",
                "last_scheduler_context": {
                    "readback_compare_mode": "register_exact",
                    "p_compare_source": "readback",
                    "q_compare_source": "cache_fallback",
                    "p_readback_ok": True,
                    "q_readback_ok": False,
                    "p_readback_mismatch": True,
                    "q_readback_mismatch": None,
                },
            },
            dispatch_enabled=True,
        )
        self.assertEqual(len(lines), 2)
        self.assertIn("RB P/Q=mismatch/read-fail->cache", lines[0])


if __name__ == "__main__":
    unittest.main()
