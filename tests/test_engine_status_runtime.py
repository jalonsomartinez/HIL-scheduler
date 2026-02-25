import queue
import threading
import unittest
from datetime import datetime, timezone

from engine_status_runtime import default_engine_status, update_engine_status


def _shared():
    return {
        "lock": threading.Lock(),
        "control_command_queue": queue.Queue(maxsize=8),
        "control_command_status_by_id": {},
        "control_command_history_ids": [],
        "control_command_active_id": None,
        "control_engine_status": default_engine_status(include_last_observed_refresh=True),
    }


class EngineStatusRuntimeTests(unittest.TestCase):
    def test_updates_queue_counts_and_active_metadata(self):
        shared = _shared()
        shared["control_command_status_by_id"] = {
            "cmd-1": {"id": "cmd-1", "kind": "plant.start", "state": "queued", "started_at": None},
            "cmd-2": {"id": "cmd-2", "kind": "plant.stop", "state": "running", "started_at": "ts"},
            "cmd-3": {"id": "cmd-3", "kind": "plant.stop", "state": "failed", "started_at": "ts"},
        }
        shared["control_command_history_ids"] = ["cmd-1", "cmd-2", "cmd-3"]
        shared["control_command_active_id"] = "cmd-2"
        shared["control_command_queue"].put_nowait({"id": "cmd-1"})
        now_value = datetime(2026, 2, 25, 12, 0, tzinfo=timezone.utc)

        status = update_engine_status(
            shared,
            status_key="control_engine_status",
            queue_key="control_command_queue",
            status_by_id_key="control_command_status_by_id",
            history_ids_key="control_command_history_ids",
            active_id_key="control_command_active_id",
            failed_recent_window=20,
            now_value=now_value,
            set_alive=True,
            last_loop_start=now_value,
            extra_updates={"last_observed_refresh": now_value},
            include_last_observed_refresh=True,
        )

        self.assertTrue(status["alive"])
        self.assertIn(status["queue_depth"], (0, 1))
        self.assertEqual(status["queued_count"], 1)
        self.assertEqual(status["running_count"], 1)
        self.assertEqual(status["failed_recent_count"], 1)
        self.assertEqual(status["active_command_id"], "cmd-2")
        self.assertEqual(status["active_command_kind"], "plant.stop")
        self.assertEqual(status["active_command_started_at"], "ts")
        self.assertEqual(status["last_observed_refresh"], now_value)

    def test_last_finished_and_exception_are_published(self):
        shared = _shared()
        now_value = datetime(2026, 2, 25, 12, 0, tzinfo=timezone.utc)
        status = update_engine_status(
            shared,
            status_key="control_engine_status",
            queue_key="control_command_queue",
            status_by_id_key="control_command_status_by_id",
            history_ids_key="control_command_history_ids",
            active_id_key="control_command_active_id",
            failed_recent_window=20,
            now_value=now_value,
            last_exception={"timestamp": now_value, "message": "boom"},
            last_finished_command={"id": "cmd-9", "state": "failed"},
            last_loop_end=now_value,
            include_last_observed_refresh=True,
        )
        self.assertEqual(status["last_exception"]["message"], "boom")
        self.assertEqual(status["last_finished_command"]["id"], "cmd-9")
        self.assertEqual(status["last_loop_end"], now_value)


if __name__ == "__main__":
    unittest.main()
