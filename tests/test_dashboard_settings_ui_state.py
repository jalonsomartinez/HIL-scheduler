import unittest
from datetime import datetime, timezone

from dashboard.settings_ui_state import (
    api_connection_controls_state,
    api_connection_display_state,
    manual_series_controls_state,
    manual_series_display_state,
    posting_controls_state,
    posting_display_state,
    resolve_command_click_feedback_state,
)


class DashboardSettingsUiStateTests(unittest.TestCase):
    def test_click_feedback_transition_within_hold(self):
        now_ts = datetime(2026, 2, 25, 12, 0, 2, tzinfo=timezone.utc)
        start_ms = int(datetime(2026, 2, 25, 12, 0, 1, tzinfo=timezone.utc).timestamp() * 1000)
        state = resolve_command_click_feedback_state(
            positive_click_ts_ms=start_ms,
            negative_click_ts_ms=None,
            positive_state="connecting",
            negative_state="disconnecting",
            now_ts=now_ts,
            hold_seconds=2.0,
        )
        self.assertEqual(state, "connecting")

    def test_manual_controls_enable_update_only_when_active_and_dirty(self):
        display = manual_series_display_state("active", None)
        controls = manual_series_controls_state(display, has_draft_rows=True, is_dirty=True)
        self.assertFalse(controls["update_disabled"])
        self.assertTrue(controls["activate_disabled"])
        self.assertFalse(controls["inactivate_disabled"])
        self.assertEqual(controls["activate_label"], "Active")
        self.assertEqual(controls["inactivate_label"], "Inactive")

    def test_manual_controls_disable_update_when_inactive(self):
        controls = manual_series_controls_state("inactive", has_draft_rows=True, is_dirty=True)
        self.assertTrue(controls["update_disabled"])
        self.assertFalse(controls["activate_disabled"])
        self.assertEqual(controls["activate_label"], "Activate")
        self.assertEqual(controls["inactivate_label"], "Inactive")

    def test_manual_controls_transition_labels(self):
        activating = manual_series_controls_state("activating", has_draft_rows=True, is_dirty=False)
        self.assertEqual(activating["activate_label"], "Activating...")
        self.assertEqual(activating["inactivate_label"], "Inactive")

        inactivating = manual_series_controls_state("inactivating", has_draft_rows=True, is_dirty=False)
        self.assertEqual(inactivating["activate_label"], "Activate")
        self.assertEqual(inactivating["inactivate_label"], "Inactivating...")

    def test_api_connection_error_overrides_connected_display(self):
        self.assertEqual(api_connection_display_state("connected", None, derived_error=True), "error")
        controls = api_connection_controls_state("connecting")
        self.assertEqual(controls["connect_label"], "Connecting...")
        self.assertTrue(controls["connect_disabled"])

    def test_api_connection_terminal_button_labels(self):
        connected = api_connection_controls_state("connected")
        self.assertEqual(connected["connect_label"], "Connected")
        self.assertEqual(connected["disconnect_label"], "Disconnect")
        self.assertTrue(connected["connect_disabled"])

        disconnected = api_connection_controls_state("disconnected")
        self.assertEqual(disconnected["connect_label"], "Connect")
        self.assertEqual(disconnected["disconnect_label"], "Disconnected")
        self.assertTrue(disconnected["disconnect_disabled"])

    def test_posting_controls_states(self):
        self.assertEqual(posting_display_state("enabled", None), "enabled")
        controls = posting_controls_state("disabling")
        self.assertTrue(controls["enable_disabled"])
        self.assertTrue(controls["disable_disabled"])


if __name__ == "__main__":
    unittest.main()
