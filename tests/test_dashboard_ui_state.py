import unittest
from datetime import datetime, timezone

from dashboard.ui_state import resolve_click_feedback_transition_state, resolve_runtime_transition_state


class DashboardUiStateTests(unittest.TestCase):
    def test_runtime_transition_prefers_observed_enable_state(self):
        self.assertEqual(resolve_runtime_transition_state("running", 0), "stopped")
        self.assertEqual(resolve_runtime_transition_state("stopped", 1), "running")
        self.assertEqual(resolve_runtime_transition_state("starting", 1), "running")
        self.assertEqual(resolve_runtime_transition_state("stopping", 0), "stopped")
        self.assertEqual(resolve_runtime_transition_state("starting", 0), "starting")
        self.assertEqual(resolve_runtime_transition_state("stopping", 1), "stopping")

    def test_click_feedback_holds_latest_transition_for_short_window(self):
        now_ts = datetime(2026, 2, 25, 12, 0, 1, tzinfo=timezone.utc)
        now_ms = int(now_ts.timestamp() * 1000)
        self.assertEqual(
            resolve_click_feedback_transition_state(
                start_click_ts_ms=now_ms - 500,
                stop_click_ts_ms=None,
                now_ts=now_ts,
                hold_seconds=1.5,
            ),
            "starting",
        )
        self.assertEqual(
            resolve_click_feedback_transition_state(
                start_click_ts_ms=now_ms - 1400,
                stop_click_ts_ms=now_ms - 200,
                now_ts=now_ts,
                hold_seconds=1.5,
            ),
            "stopping",
        )
        self.assertIsNone(
            resolve_click_feedback_transition_state(
                start_click_ts_ms=now_ms - 2500,
                stop_click_ts_ms=None,
                now_ts=now_ts,
                hold_seconds=1.5,
            )
        )


if __name__ == "__main__":
    unittest.main()
