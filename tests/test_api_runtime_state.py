import threading
import unittest
from datetime import datetime, timezone

from runtime.api_runtime_state import (
    complete_api_connect_probe,
    complete_api_disconnect,
    default_api_connection_runtime,
    ensure_api_connection_runtime,
    publish_api_fetch_health,
    publish_api_posting_health,
    recompute_api_connection_runtime,
    set_api_connection_transition,
)


def _shared():
    return {"lock": threading.Lock(), "api_connection_runtime": default_api_connection_runtime()}


class ApiRuntimeStateTests(unittest.TestCase):
    def test_disconnected_desired_state_wins_over_stale_subhealth(self):
        shared = _shared()
        now_value = datetime(2026, 2, 25, 12, 0, tzinfo=timezone.utc)
        publish_api_fetch_health(
            shared,
            state="error",
            now_value=now_value,
            error={"timestamp": now_value, "code": "fetch_failed", "message": "boom"},
        )
        complete_api_disconnect(shared, now_value=now_value)
        runtime = ensure_api_connection_runtime(shared)
        self.assertEqual(runtime["desired_state"], "disconnected")
        self.assertEqual(runtime["state"], "disconnected")
        self.assertFalse(runtime["connected"])
        self.assertIsNone(runtime["last_error"])

    def test_connected_intent_plus_fetch_or_posting_error_sets_error(self):
        shared = _shared()
        t0 = datetime(2026, 2, 25, 12, 0, tzinfo=timezone.utc)
        complete_api_connect_probe(shared, success=True, now_value=t0, command_id="cmd-1")
        runtime = ensure_api_connection_runtime(shared)
        self.assertEqual(runtime["state"], "connected")
        publish_api_fetch_health(
            shared,
            state="error",
            now_value=t0,
            error={"timestamp": t0, "code": "fetch_failed", "message": "fetch failed"},
        )
        self.assertEqual(ensure_api_connection_runtime(shared)["state"], "error")
        publish_api_fetch_health(shared, state="ok", now_value=t0, last_success=t0)
        publish_api_posting_health(
            shared,
            state="error",
            now_value=t0,
            error={"timestamp": t0, "code": "post_failed", "message": "post failed"},
        )
        self.assertEqual(ensure_api_connection_runtime(shared)["state"], "error")

    def test_connected_when_intent_connected_and_no_subhealth_errors(self):
        shared = _shared()
        t0 = datetime(2026, 2, 25, 12, 0, tzinfo=timezone.utc)
        complete_api_connect_probe(shared, success=True, now_value=t0, command_id="cmd-1")
        publish_api_posting_health(shared, state="idle", now_value=t0)
        runtime = ensure_api_connection_runtime(shared)
        self.assertEqual(runtime["state"], "connected")
        self.assertTrue(runtime["connected"])
        self.assertIsNone(runtime["last_error"])

    def test_transition_state_preserved_until_completion(self):
        shared = _shared()
        t0 = datetime(2026, 2, 25, 12, 0, tzinfo=timezone.utc)
        set_api_connection_transition(shared, state="connecting", desired_state="connected", now_value=t0, command_id="cmd-1")
        publish_api_fetch_health(
            shared,
            state="error",
            now_value=t0,
            error={"timestamp": t0, "code": "fetch_failed", "message": "boom"},
        )
        runtime = ensure_api_connection_runtime(shared)
        self.assertEqual(runtime["state"], "connecting")
        complete_api_connect_probe(shared, success=False, now_value=t0, command_id="cmd-1", error={"timestamp": t0, "code": "connect_failed", "message": "probe"})
        runtime = ensure_api_connection_runtime(shared)
        self.assertEqual(runtime["state"], "error")

    def test_last_error_clears_on_recovery_and_disconnect(self):
        shared = _shared()
        t0 = datetime(2026, 2, 25, 12, 0, tzinfo=timezone.utc)
        t1 = datetime(2026, 2, 25, 12, 1, tzinfo=timezone.utc)
        complete_api_connect_probe(
            shared,
            success=False,
            now_value=t0,
            command_id="cmd-1",
            error={"timestamp": t0, "code": "connect_failed", "message": "probe failed"},
        )
        self.assertEqual(ensure_api_connection_runtime(shared)["state"], "error")
        complete_api_connect_probe(shared, success=True, now_value=t1, command_id="cmd-2")
        runtime = ensure_api_connection_runtime(shared)
        self.assertEqual(runtime["state"], "connected")
        self.assertIsNone(runtime["last_error"])
        complete_api_disconnect(shared, now_value=t1)
        runtime = ensure_api_connection_runtime(shared)
        self.assertEqual(runtime["state"], "disconnected")
        self.assertIsNone(runtime["last_error"])

    def test_recompute_normalizes_missing_nested_health(self):
        shared = {"lock": threading.Lock(), "api_connection_runtime": {"state": "connected", "desired_state": "connected"}}
        runtime = recompute_api_connection_runtime(shared, now_value=datetime(2026, 2, 25, 12, 0, tzinfo=timezone.utc))
        self.assertIn("fetch_health", runtime)
        self.assertIn("posting_health", runtime)


if __name__ == "__main__":
    unittest.main()
