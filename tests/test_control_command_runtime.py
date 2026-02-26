import queue
import threading
import unittest
from datetime import datetime, timezone

from control.command_runtime import (
    enqueue_control_command,
    get_next_command_id,
    mark_command_finished,
    mark_command_running,
)


def _shared_data(queue_maxsize=8):
    return {
        "lock": threading.Lock(),
        "control_command_queue": queue.Queue(maxsize=queue_maxsize),
        "control_command_status_by_id": {},
        "control_command_history_ids": [],
        "control_command_active_id": None,
        "control_command_next_id": 1,
    }


class ControlCommandRuntimeTests(unittest.TestCase):
    def test_get_next_command_id_is_monotonic(self):
        shared_data = _shared_data()
        self.assertEqual(get_next_command_id(shared_data), "cmd-000001")
        self.assertEqual(get_next_command_id(shared_data), "cmd-000002")

    def test_enqueue_control_command_success_creates_queued_status(self):
        shared_data = _shared_data()
        now_value = datetime(2026, 2, 25, 12, 0, tzinfo=timezone.utc)

        status = enqueue_control_command(
            shared_data,
            kind="plant.start",
            payload={"plant_id": "lib"},
            source="dashboard",
            now_fn=lambda: now_value,
        )

        self.assertEqual(status["id"], "cmd-000001")
        self.assertEqual(status["kind"], "plant.start")
        self.assertEqual(status["state"], "queued")
        queued = shared_data["control_command_queue"].get_nowait()
        self.assertEqual(queued["id"], "cmd-000001")
        self.assertEqual(queued["payload"], {"plant_id": "lib"})

    def test_enqueue_control_command_queue_full_returns_rejected(self):
        shared_data = _shared_data(queue_maxsize=1)
        shared_data["control_command_queue"].put_nowait({"id": "occupied"})

        status = enqueue_control_command(
            shared_data,
            kind="plant.stop",
            payload={"plant_id": "lib"},
            source="dashboard",
            now_fn=lambda: datetime(2026, 2, 25, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(status["state"], "rejected")
        self.assertEqual(status["message"], "queue_full")

    def test_mark_command_running_and_finished_updates_active_id(self):
        shared_data = _shared_data()
        status = enqueue_control_command(
            shared_data,
            kind="plant.start",
            payload={"plant_id": "lib"},
            source="dashboard",
            now_fn=lambda: datetime(2026, 2, 25, 12, 0, tzinfo=timezone.utc),
        )

        mark_command_running(shared_data, status["id"], started_at=datetime(2026, 2, 25, 12, 1, tzinfo=timezone.utc))
        self.assertEqual(shared_data["control_command_active_id"], status["id"])
        self.assertEqual(shared_data["control_command_status_by_id"][status["id"]]["state"], "running")

        final_status = mark_command_finished(
            shared_data,
            status["id"],
            state="succeeded",
            result={"ok": True},
            finished_at=datetime(2026, 2, 25, 12, 2, tzinfo=timezone.utc),
        )
        self.assertEqual(final_status["state"], "succeeded")
        self.assertIsNone(shared_data["control_command_active_id"])

    def test_history_retention_prunes_oldest_statuses(self):
        shared_data = _shared_data(queue_maxsize=400)
        for i in range(205):
            enqueue_control_command(
                shared_data,
                kind="plant.record_stop",
                payload={"plant_id": "lib"},
                source="dashboard",
                now_fn=lambda i=i: datetime(2026, 2, 25, 12, 0, i % 60, tzinfo=timezone.utc),
            )

        history = shared_data["control_command_history_ids"]
        status_by_id = shared_data["control_command_status_by_id"]
        self.assertEqual(len(history), 200)
        self.assertEqual(history[0], "cmd-000006")
        self.assertNotIn("cmd-000001", status_by_id)
        self.assertIn("cmd-000205", status_by_id)


if __name__ == "__main__":
    unittest.main()
