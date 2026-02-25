import queue
import threading
import unittest
from datetime import datetime, timezone

from settings_command_runtime import (
    enqueue_settings_command,
    get_next_command_id,
    mark_command_finished,
    mark_command_running,
)


def _shared_data(queue_maxsize=8):
    return {
        "lock": threading.Lock(),
        "settings_command_queue": queue.Queue(maxsize=queue_maxsize),
        "settings_command_status_by_id": {},
        "settings_command_history_ids": [],
        "settings_command_active_id": None,
        "settings_command_next_id": 1,
    }


class SettingsCommandRuntimeTests(unittest.TestCase):
    def test_get_next_command_id_is_monotonic(self):
        shared = _shared_data()
        self.assertEqual(get_next_command_id(shared), "cmd-000001")
        self.assertEqual(get_next_command_id(shared), "cmd-000002")

    def test_enqueue_success(self):
        shared = _shared_data()
        status = enqueue_settings_command(
            shared,
            kind="api.connect",
            payload={"password": "pw"},
            source="dashboard",
            now_fn=lambda: datetime(2026, 2, 25, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(status["state"], "queued")
        queued = shared["settings_command_queue"].get_nowait()
        self.assertEqual(queued["kind"], "api.connect")

    def test_queue_full_rejected(self):
        shared = _shared_data(queue_maxsize=1)
        shared["settings_command_queue"].put_nowait({"id": "occupied"})
        status = enqueue_settings_command(
            shared,
            kind="posting.enable",
            payload={},
            source="dashboard",
            now_fn=lambda: datetime(2026, 2, 25, 12, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(status["state"], "rejected")
        self.assertEqual(status["message"], "queue_full")

    def test_mark_running_and_finished(self):
        shared = _shared_data()
        status = enqueue_settings_command(
            shared,
            kind="posting.disable",
            payload={},
            source="dashboard",
            now_fn=lambda: datetime(2026, 2, 25, 12, 0, tzinfo=timezone.utc),
        )
        mark_command_running(shared, status["id"], started_at=datetime(2026, 2, 25, 12, 1, tzinfo=timezone.utc))
        self.assertEqual(shared["settings_command_active_id"], status["id"])
        final = mark_command_finished(
            shared,
            status["id"],
            state="succeeded",
            finished_at=datetime(2026, 2, 25, 12, 2, tzinfo=timezone.utc),
        )
        self.assertEqual(final["state"], "succeeded")
        self.assertIsNone(shared["settings_command_active_id"])


if __name__ == "__main__":
    unittest.main()

