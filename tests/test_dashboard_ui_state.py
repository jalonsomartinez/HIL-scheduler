import unittest
from datetime import datetime, timedelta, timezone

from dashboard.ui_state import (
    get_plant_power_toggle_state,
    get_recording_toggle_state,
    is_observed_state_effectively_stale,
    resolve_click_feedback_transition_state,
    resolve_runtime_transition_state,
)


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

    def test_plant_power_toggle_states(self):
        stopped = get_plant_power_toggle_state("stopped")
        self.assertEqual(stopped["positive_label"], "Run")
        self.assertEqual(stopped["negative_label"], "Stopped")
        self.assertFalse(stopped["positive_disabled"])
        self.assertTrue(stopped["negative_disabled"])
        self.assertEqual(stopped["active_side"], "negative")

        starting = get_plant_power_toggle_state("starting")
        self.assertEqual(starting["positive_label"], "Starting...")
        self.assertEqual(starting["negative_label"], "Stop")
        self.assertTrue(starting["positive_disabled"])
        self.assertTrue(starting["negative_disabled"])
        self.assertEqual(starting["active_side"], "positive")

        running = get_plant_power_toggle_state("running")
        self.assertEqual(running["positive_label"], "Running")
        self.assertEqual(running["negative_label"], "Stop")
        self.assertTrue(running["positive_disabled"])
        self.assertFalse(running["negative_disabled"])
        self.assertEqual(running["active_side"], "positive")

        stopping = get_plant_power_toggle_state("stopping")
        self.assertEqual(stopping["positive_label"], "Run")
        self.assertEqual(stopping["negative_label"], "Stopping...")
        self.assertEqual(stopping["active_side"], "negative")

        unknown = get_plant_power_toggle_state("unknown")
        self.assertEqual(unknown["positive_label"], "Run")
        self.assertEqual(unknown["negative_label"], "Stop")
        self.assertFalse(unknown["positive_disabled"])
        self.assertTrue(unknown["negative_disabled"])
        self.assertIsNone(unknown["active_side"])

    def test_recording_toggle_states(self):
        idle = get_recording_toggle_state(False)
        self.assertEqual(idle["positive_label"], "Record")
        self.assertEqual(idle["negative_label"], "Stopped")
        self.assertEqual(idle["active_side"], "negative")
        self.assertFalse(idle["positive_disabled"])
        self.assertTrue(idle["negative_disabled"])

        starting = get_recording_toggle_state(False, click_feedback_state="starting")
        self.assertEqual(starting["positive_label"], "Starting...")
        self.assertEqual(starting["negative_label"], "Stop")
        self.assertEqual(starting["active_side"], "positive")
        self.assertTrue(starting["positive_disabled"])
        self.assertTrue(starting["negative_disabled"])

        active = get_recording_toggle_state(True)
        self.assertEqual(active["positive_label"], "Recording")
        self.assertEqual(active["negative_label"], "Stop")
        self.assertEqual(active["active_side"], "positive")
        self.assertTrue(active["positive_disabled"])
        self.assertFalse(active["negative_disabled"])

        stopping = get_recording_toggle_state(True, click_feedback_state="stopping")
        self.assertEqual(stopping["positive_label"], "Record")
        self.assertEqual(stopping["negative_label"], "Stopping...")
        self.assertEqual(stopping["active_side"], "negative")
        self.assertTrue(stopping["positive_disabled"])
        self.assertTrue(stopping["negative_disabled"])

    def test_observed_state_effective_stale_uses_age_guard(self):
        now_ts = datetime(2026, 2, 26, 12, 0, 10, tzinfo=timezone.utc)
        self.assertTrue(
            is_observed_state_effectively_stale(
                {"stale": True, "last_success": now_ts.isoformat()},
                now_ts=now_ts,
            )
        )
        self.assertFalse(
            is_observed_state_effectively_stale(
                {"stale": False, "last_success": (now_ts - timedelta(seconds=2)).isoformat()},
                now_ts=now_ts,
                stale_after_s=3.0,
            )
        )
        self.assertTrue(
            is_observed_state_effectively_stale(
                {"stale": False, "last_success": (now_ts - timedelta(seconds=5)).isoformat()},
                now_ts=now_ts,
                stale_after_s=3.0,
            )
        )
        self.assertTrue(
            is_observed_state_effectively_stale(
                {"stale": False, "last_success": None},
                now_ts=now_ts,
            )
        )


if __name__ == "__main__":
    unittest.main()
