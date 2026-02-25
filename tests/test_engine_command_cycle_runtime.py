import queue
import threading
import unittest
from datetime import datetime, timezone

from engine_command_cycle_runtime import run_command_with_lifecycle


class EngineCommandCycleRuntimeTests(unittest.TestCase):
    def test_success_path_marks_running_and_finished_and_updates_status(self):
        shared = {"lock": threading.Lock()}
        q = queue.Queue()
        command = {"id": "cmd-000001", "kind": "x"}
        q.put_nowait(command)
        dequeued = q.get_nowait()

        calls = {"running": [], "finished": [], "status": []}
        now_values = [
            datetime(2026, 2, 25, 12, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 2, 25, 12, 0, 1, tzinfo=timezone.utc),
            datetime(2026, 2, 25, 12, 0, 2, tzinfo=timezone.utc),
        ]

        def _now():
            return now_values.pop(0) if now_values else datetime(2026, 2, 25, 12, 0, 3, tzinfo=timezone.utc)

        def _mark_running(_shared, command_id, *, started_at):
            calls["running"].append((command_id, started_at))

        def _mark_finished(_shared, command_id, *, state, message=None, result=None, finished_at=None):
            calls["finished"].append((command_id, state, message, result, finished_at))
            return {
                "id": command_id,
                "kind": "x",
                "state": state,
                "message": message,
                "finished_at": finished_at,
            }

        def _update_status(_shared, **kwargs):
            calls["status"].append(kwargs)

        command_id = run_command_with_lifecycle(
            shared,
            queue_obj=q,
            command=dequeued,
            now_fn=_now,
            execute_command_fn=lambda _cmd: {"state": "succeeded", "message": None, "result": {"ok": True}},
            mark_command_running_fn=_mark_running,
            mark_command_finished_fn=_mark_finished,
            update_engine_status_fn=_update_status,
            exception_log_prefix="TestEngine",
            set_last_loop_end=True,
        )

        self.assertEqual(command_id, "cmd-000001")
        self.assertEqual(calls["running"][0][0], "cmd-000001")
        self.assertEqual(calls["finished"][0][1], "succeeded")
        self.assertEqual(q.unfinished_tasks, 0)
        self.assertEqual(len(calls["status"]), 1)
        self.assertIn("last_finished_command", calls["status"][0])
        self.assertIn("last_loop_end", calls["status"][0])

    def test_exception_path_publishes_last_exception_and_failed_terminal_status(self):
        shared = {"lock": threading.Lock()}
        q = queue.Queue()
        command = {"id": "cmd-000002", "kind": "x"}
        q.put_nowait(command)
        dequeued = q.get_nowait()

        calls = {"finished": [], "status": []}

        def _now():
            return datetime(2026, 2, 25, 12, 1, tzinfo=timezone.utc)

        def _mark_running(_shared, command_id, *, started_at):
            return None

        def _mark_finished(_shared, command_id, *, state, message=None, result=None, finished_at=None):
            calls["finished"].append((command_id, state, message, result, finished_at))
            return {
                "id": command_id,
                "kind": "x",
                "state": state,
                "message": message,
                "finished_at": finished_at,
            }

        def _update_status(_shared, **kwargs):
            calls["status"].append(kwargs)

        command_id = run_command_with_lifecycle(
            shared,
            queue_obj=q,
            command=dequeued,
            now_fn=_now,
            execute_command_fn=lambda _cmd: (_ for _ in ()).throw(RuntimeError("boom")),
            mark_command_running_fn=_mark_running,
            mark_command_finished_fn=_mark_finished,
            update_engine_status_fn=_update_status,
            exception_log_prefix="TestEngine",
        )

        self.assertEqual(command_id, "cmd-000002")
        self.assertEqual(calls["finished"][0][1], "failed")
        self.assertEqual(calls["finished"][0][2], "boom")
        self.assertEqual(q.unfinished_tasks, 0)
        self.assertEqual(len(calls["status"]), 2)
        self.assertIn("last_exception", calls["status"][0])
        self.assertIn("last_finished_command", calls["status"][1])


if __name__ == "__main__":
    unittest.main()
