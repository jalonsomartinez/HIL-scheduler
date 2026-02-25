import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

import manual_schedule_manager as msm
from dashboard_settings_intents import (
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
        self.assertEqual(len(intent["payload"]["series_rows"]), 1)
        self.assertIn("datetime", intent["payload"]["series_rows"][0])

    def test_manual_inactivate_has_no_series_rows(self):
        intent = manual_settings_intent_from_trigger(
            "manual-toggle-vrfb-q-disable-btn",
            draft_series_by_key=msm.default_manual_series_map(),
            tz=ZoneInfo("Europe/Madrid"),
        )
        self.assertEqual(intent["kind"], "manual.inactivate")
        self.assertEqual(intent["payload"], {"series_key": "vrfb_q"})

    def test_api_connect_uses_input_password_when_provided(self):
        intent = api_connection_intent_from_trigger("set-password-btn", password_value=" pw ")
        self.assertEqual(intent["kind"], "api.connect")
        self.assertEqual(intent["payload"]["password"], "pw")

    def test_api_connect_allows_null_password_to_use_stored(self):
        intent = api_connection_intent_from_trigger("set-password-btn", password_value="")
        self.assertEqual(intent["kind"], "api.connect")
        self.assertIsNone(intent["payload"]["password"])

    def test_posting_intents(self):
        self.assertEqual(posting_intent_from_trigger("api-posting-enable-btn")["kind"], "posting.enable")
        self.assertEqual(posting_intent_from_trigger("api-posting-disable-btn")["kind"], "posting.disable")
        self.assertIsNone(posting_intent_from_trigger("other"))


if __name__ == "__main__":
    unittest.main()

