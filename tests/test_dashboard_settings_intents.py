import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

import scheduling.manual_schedule_manager as msm
from dashboard.settings_intents import (
    api_password_intent_from_trigger,
    api_connection_intent_from_trigger,
    manual_settings_intent_from_trigger,
    posting_intent_from_trigger,
)


class DashboardSettingsIntentsTests(unittest.TestCase):
    def test_manual_activate_uses_full_series_payload(self):
        tz = ZoneInfo("Europe/Madrid")
        df = pd.DataFrame(
            [{"datetime": datetime(2026, 2, 25, 10, 0, tzinfo=tz), "setpoint": 1.0}]
        ).set_index("datetime")
        intent = manual_settings_intent_from_trigger(
            "manual-toggle-lib-p-enable-btn",
            draft_series_by_key={"lib_p": df},
            tz=tz,
        )
        self.assertEqual(intent["kind"], "manual.activate")
        self.assertEqual(intent["payload"]["series_key"], "lib_p")
        self.assertEqual(len(intent["payload"]["series_rows"]), 2)
        self.assertIn("datetime", intent["payload"]["series_rows"][0])
        self.assertEqual(intent["payload"]["series_rows"][0]["setpoint"], 1.0)
        self.assertEqual(intent["payload"]["series_rows"][-1]["setpoint"], 1.0)

    def test_manual_inactivate_has_no_series_rows(self):
        intent = manual_settings_intent_from_trigger(
            "manual-toggle-vrfb-q-disable-btn",
            draft_series_by_key=msm.default_manual_series_map(),
            tz=ZoneInfo("Europe/Madrid"),
        )
        self.assertEqual(intent["kind"], "manual.inactivate")
        self.assertEqual(intent["payload"], {"series_key": "vrfb_q"})

    def test_manual_update_serializes_terminal_duplicate_row(self):
        tz = ZoneInfo("Europe/Madrid")
        df = pd.DataFrame(
            [{"datetime": datetime(2026, 2, 25, 10, 0, tzinfo=tz), "setpoint": 5.0}]
        ).set_index("datetime")
        intent = manual_settings_intent_from_trigger(
            "manual-toggle-lib-q-update-btn",
            draft_series_by_key={"lib_q": df},
            tz=tz,
        )
        self.assertEqual(intent["kind"], "manual.update")
        self.assertEqual(len(intent["payload"]["series_rows"]), 2)
        self.assertEqual(intent["payload"]["series_rows"][0]["setpoint"], 5.0)
        self.assertEqual(intent["payload"]["series_rows"][-1]["setpoint"], 5.0)

    def test_api_password_set_uses_trimmed_password(self):
        intent = api_password_intent_from_trigger("save-api-password-btn", password_value=" pw ")
        self.assertEqual(intent["kind"], "api.password.set")
        self.assertEqual(intent["payload"]["password"], "pw")

    def test_api_password_set_allows_blank_for_engine_rejection(self):
        intent = api_password_intent_from_trigger("save-api-password-btn", password_value="")
        self.assertEqual(intent["kind"], "api.password.set")
        self.assertIsNone(intent["payload"]["password"])

    def test_api_connect_uses_new_trigger(self):
        intent = api_connection_intent_from_trigger("connect-api-btn")
        self.assertEqual(intent["kind"], "api.connect")
        self.assertEqual(intent["payload"], {})

    def test_api_connect_keeps_legacy_trigger_compatibility(self):
        intent = api_connection_intent_from_trigger("set-password-btn")
        self.assertEqual(intent["kind"], "api.connect")
        self.assertEqual(intent["payload"], {})

    def test_posting_intents(self):
        self.assertEqual(posting_intent_from_trigger("api-posting-enable-btn")["kind"], "posting.enable")
        self.assertEqual(posting_intent_from_trigger("api-posting-disable-btn")["kind"], "posting.disable")
        self.assertIsNone(posting_intent_from_trigger("other"))


if __name__ == "__main__":
    unittest.main()
