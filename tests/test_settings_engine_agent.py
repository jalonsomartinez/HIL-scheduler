import queue
import threading
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd

import manual_schedule_manager as msm
from settings_engine_agent import _execute_settings_command, _run_single_settings_cycle


class _FakeAPI:
    should_fail = False
    password_seen = None
    login_calls = 0

    def __init__(self, base_url=None, email=None, timezone_name=None):
        self._password = None

    def set_password(self, password):
        type(self).password_seen = password
        self._password = password

    def login(self):
        type(self).login_calls += 1
        if type(self).should_fail:
            raise RuntimeError("probe failed")
        return "token"


def _config():
    return {
        "TIMEZONE_NAME": "Europe/Madrid",
        "PLANT_IDS": ("lib", "vrfb"),
        "ISTENTORE_BASE_URL": "https://example.invalid",
        "ISTENTORE_EMAIL": "user@example.com",
        "ISTENTORE_POST_MEASUREMENTS_IN_API_MODE": True,
    }


def _shared():
    return {
        "lock": threading.Lock(),
        "shutdown_event": threading.Event(),
        "settings_command_queue": queue.Queue(maxsize=16),
        "settings_command_status_by_id": {},
        "settings_command_history_ids": [],
        "settings_command_active_id": None,
        "settings_command_next_id": 1,
        "settings_engine_status": {},
        "manual_schedule_series_df_by_key": msm.default_manual_series_map(),
        "manual_schedule_draft_series_df_by_key": msm.default_manual_series_map(),
        "manual_schedule_merge_enabled_by_key": msm.default_manual_merge_enabled_map(default_enabled=False),
        "manual_schedule_df_by_plant": {"lib": pd.DataFrame(), "vrfb": pd.DataFrame()},
        "manual_series_runtime_state_by_key": {},
        "api_password": None,
        "api_connection_runtime": {
            "state": "disconnected",
            "connected": False,
            "desired_state": "disconnected",
            "fetch_health": {"state": "disabled", "last_success": None, "last_error": None, "last_attempt": None},
            "posting_health": {"state": "disabled", "last_success": None, "last_error": None, "last_attempt": None},
        },
        "posting_runtime": {"state": "enabled", "policy_enabled": True, "desired_state": "enabled"},
        "measurement_posting_enabled": True,
        "data_fetcher_status": {"connected": False, "error": None},
    }


class SettingsEngineAgentTests(unittest.TestCase):
    def test_manual_activate_updates_applied_series_and_runtime_state(self):
        shared = _shared()
        cfg = _config()
        payload = {
            "series_key": "lib_p",
            "series_rows": [
                {"datetime": "2026-02-25T10:00:00+01:00", "setpoint": 1.0},
                {"datetime": "2026-02-25T10:15:00+01:00", "setpoint": 2.0},
            ],
        }
        result = _execute_settings_command(cfg, shared, {"id": "cmd-1", "kind": "manual.activate", "payload": payload}, tz=timezone.utc)
        self.assertEqual(result["state"], "succeeded")
        with shared["lock"]:
            self.assertTrue(shared["manual_schedule_merge_enabled_by_key"]["lib_p"])
            self.assertEqual(shared["manual_series_runtime_state_by_key"]["lib_p"]["state"], "active")
            self.assertEqual(len(shared["manual_schedule_series_df_by_key"]["lib_p"]), 2)

    def test_manual_update_requires_active(self):
        shared = _shared()
        cfg = _config()
        result = _execute_settings_command(
            cfg,
            shared,
            {"id": "cmd-2", "kind": "manual.update", "payload": {"series_key": "lib_q", "series_rows": []}},
            tz=timezone.utc,
        )
        self.assertEqual(result["state"], "rejected")
        self.assertEqual(result["message"], "not_active")

    def test_manual_inactivate_disables_merge(self):
        shared = _shared()
        cfg = _config()
        shared["manual_schedule_merge_enabled_by_key"]["vrfb_q"] = True
        shared["manual_series_runtime_state_by_key"] = {
            "vrfb_q": {"state": "active", "desired_state": "active", "active": True, "applied_series_df": pd.DataFrame(columns=["setpoint"])}
        }
        # ensure missing keys are tolerated
        result = _execute_settings_command(
            cfg,
            shared,
            {"id": "cmd-3", "kind": "manual.inactivate", "payload": {"series_key": "vrfb_q"}},
            tz=timezone.utc,
        )
        self.assertEqual(result["state"], "succeeded")
        with shared["lock"]:
            self.assertFalse(shared["manual_schedule_merge_enabled_by_key"]["vrfb_q"])
            self.assertEqual(shared["manual_series_runtime_state_by_key"]["vrfb_q"]["state"], "inactive")

    def test_api_connect_stores_password_and_sets_connected(self):
        _FakeAPI.should_fail = False
        _FakeAPI.password_seen = None
        _FakeAPI.login_calls = 0
        shared = _shared()
        cfg = _config()
        with patch("settings_engine_agent.IstentoreAPI", _FakeAPI), patch(
            "settings_engine_agent.now_tz",
            return_value=datetime(2026, 2, 25, 12, 0, tzinfo=timezone.utc),
        ):
            result = _execute_settings_command(
                cfg,
                shared,
                {"id": "cmd-4", "kind": "api.connect", "payload": {"password": "pw"}},
                tz=timezone.utc,
            )
        self.assertEqual(result["state"], "succeeded")
        with shared["lock"]:
            self.assertEqual(shared["api_password"], "pw")
            self.assertEqual(shared["api_connection_runtime"]["state"], "connected")
            self.assertEqual(shared["api_connection_runtime"]["fetch_health"]["state"], "ok")
        self.assertEqual(_FakeAPI.password_seen, "pw")
        self.assertEqual(_FakeAPI.login_calls, 1)

    def test_api_connect_without_any_password_rejected(self):
        shared = _shared()
        cfg = _config()
        result = _execute_settings_command(
            cfg,
            shared,
            {"id": "cmd-5", "kind": "api.connect", "payload": {"password": None}},
            tz=timezone.utc,
        )
        self.assertEqual(result["state"], "rejected")
        self.assertEqual(result["message"], "missing_password")
        with shared["lock"]:
            self.assertEqual(shared["api_connection_runtime"]["state"], "error")
            self.assertEqual(shared["api_connection_runtime"]["fetch_health"]["state"], "error")

    def test_api_disconnect_preserves_password(self):
        shared = _shared()
        cfg = _config()
        shared["api_password"] = "pw"
        shared["api_connection_runtime"] = {
            "state": "connected",
            "connected": True,
            "desired_state": "connected",
            "fetch_health": {"state": "ok", "last_success": None, "last_error": None, "last_attempt": None},
            "posting_health": {"state": "idle", "last_success": None, "last_error": None, "last_attempt": None},
        }
        result = _execute_settings_command(
            cfg,
            shared,
            {"id": "cmd-6", "kind": "api.disconnect", "payload": {}},
            tz=timezone.utc,
        )
        self.assertEqual(result["state"], "succeeded")
        with shared["lock"]:
            self.assertEqual(shared["api_password"], "pw")
            self.assertEqual(shared["api_connection_runtime"]["state"], "disconnected")
            self.assertEqual(shared["api_connection_runtime"]["fetch_health"]["state"], "disabled")

    def test_posting_enable_disable_updates_policy_runtime_and_compat_flag(self):
        shared = _shared()
        cfg = _config()
        shared["posting_runtime"]["state"] = "disabled"
        shared["posting_runtime"]["policy_enabled"] = False
        shared["measurement_posting_enabled"] = False
        result = _execute_settings_command(
            cfg,
            shared,
            {"id": "cmd-7", "kind": "posting.enable", "payload": {}},
            tz=timezone.utc,
        )
        self.assertEqual(result["state"], "succeeded")
        with shared["lock"]:
            self.assertTrue(shared["posting_runtime"]["policy_enabled"])
            self.assertEqual(shared["posting_runtime"]["state"], "enabled")
            self.assertTrue(shared["measurement_posting_enabled"])
            self.assertEqual(shared["api_connection_runtime"]["posting_health"]["state"], "idle")

    def test_single_cycle_publishes_settings_engine_status(self):
        shared = _shared()
        cfg = _config()
        shared["settings_command_status_by_id"]["cmd-000001"] = {
            "id": "cmd-000001",
            "kind": "posting.disable",
            "state": "queued",
            "payload": {},
            "source": "dashboard",
            "created_at": None,
            "started_at": None,
            "finished_at": None,
            "message": None,
            "result": None,
        }
        shared["settings_command_history_ids"].append("cmd-000001")
        shared["settings_command_queue"].put_nowait({"id": "cmd-000001", "kind": "posting.disable", "payload": {}})
        with patch("settings_engine_agent.now_tz", return_value=datetime(2026, 2, 25, 12, 0, tzinfo=timezone.utc)):
            command_id = _run_single_settings_cycle(cfg, shared, tz=timezone.utc)
        self.assertEqual(command_id, "cmd-000001")
        with shared["lock"]:
            status = dict(shared["settings_engine_status"])
            self.assertTrue(status.get("alive"))
            self.assertEqual(status.get("last_finished_command", {}).get("id"), "cmd-000001")
            self.assertIn(status.get("queue_depth"), (0, 1))


if __name__ == "__main__":
    unittest.main()
