import queue
import threading
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import pandas as pd

import manual_schedule_manager as msm
from control_command_runtime import enqueue_control_command
from control_engine_agent import _run_single_engine_cycle
from dashboard_command_intents import command_intent_from_control_trigger
from dashboard_settings_intents import posting_intent_from_trigger
from settings_command_runtime import enqueue_settings_command
from settings_engine_agent import _run_single_settings_cycle


def _control_shared():
    return {
        "lock": threading.Lock(),
        "scheduler_running_by_plant": {"lib": False, "vrfb": False},
        "plant_transition_by_plant": {"lib": "stopped", "vrfb": "stopped"},
        "measurements_filename_by_plant": {"lib": "data/lib.csv", "vrfb": None},
        "current_file_path_by_plant": {"lib": None, "vrfb": None},
        "current_file_df_by_plant": {"lib": None, "vrfb": None},
        "transport_mode": "remote",
        "transport_switching": False,
        "local_emulator_soc_seed_request_by_plant": {"lib": None, "vrfb": None},
        "local_emulator_soc_seed_result_by_plant": {
            "lib": {"request_id": None, "status": "idle", "soc_pu": None, "message": None},
            "vrfb": {"request_id": None, "status": "idle", "soc_pu": None, "message": None},
        },
        "control_command_queue": queue.Queue(maxsize=16),
        "control_command_status_by_id": {},
        "control_command_history_ids": [],
        "control_command_active_id": None,
        "control_command_next_id": 1,
        "plant_observed_state_by_plant": {
            plant_id: {
                "enable_state": None,
                "p_battery_kw": None,
                "q_battery_kvar": None,
                "last_attempt": None,
                "last_success": None,
                "error": None,
                "read_status": "unknown",
                "last_error": None,
                "consecutive_failures": 0,
                "stale": True,
            }
            for plant_id in ("lib", "vrfb")
        },
        "control_engine_status": {},
    }


def _settings_shared():
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
            "state": "connected",
            "connected": True,
            "desired_state": "connected",
            "fetch_health": {"state": "ok", "last_success": None, "last_error": None, "last_attempt": None},
            "posting_health": {"state": "idle", "last_success": None, "last_error": None, "last_attempt": None},
        },
        "posting_runtime": {"state": "enabled", "policy_enabled": True, "desired_state": "enabled"},
        "data_fetcher_status": {"connected": True, "error": None},
    }


class DashboardEngineWiringTests(unittest.TestCase):
    def test_control_intent_enqueue_and_engine_cycle_mutates_runtime(self):
        shared = _control_shared()
        config = {"PLANT_IDS": ("lib", "vrfb")}
        now_value = datetime(2026, 2, 25, 12, 0, tzinfo=timezone.utc)

        intent = command_intent_from_control_trigger("record-stop-lib")
        self.assertIsNotNone(intent)
        status = enqueue_control_command(
            shared,
            kind=intent["kind"],
            payload=intent["payload"],
            source="dashboard",
            now_fn=lambda: now_value,
        )
        self.assertEqual(status["state"], "queued")

        command_id = _run_single_engine_cycle(
            config,
            shared,
            plant_ids=("lib", "vrfb"),
            tz=timezone.utc,
            deps={"refresh_all_observed_state_fn": lambda: None},
            now_fn=lambda _cfg: now_value,
        )

        self.assertEqual(command_id, status["id"])
        with shared["lock"]:
            self.assertIsNone(shared["measurements_filename_by_plant"]["lib"])
            final_status = dict(shared["control_command_status_by_id"][status["id"]])
            self.assertEqual(final_status["state"], "succeeded")
            self.assertEqual(shared["control_engine_status"]["last_finished_command"]["id"], status["id"])

    def test_settings_intent_enqueue_and_engine_cycle_mutates_runtime(self):
        shared = _settings_shared()
        config = {
            "TIMEZONE_NAME": "Europe/Madrid",
            "PLANT_IDS": ("lib", "vrfb"),
            "ISTENTORE_POST_MEASUREMENTS_IN_API_MODE": True,
        }
        now_value = datetime(2026, 2, 25, 12, 0, tzinfo=timezone.utc)

        intent = posting_intent_from_trigger("api-posting-disable-btn")
        self.assertIsNotNone(intent)
        status = enqueue_settings_command(
            shared,
            kind=intent["kind"],
            payload=intent["payload"],
            source="dashboard",
            now_fn=lambda: now_value,
        )
        self.assertEqual(status["state"], "queued")

        with patch("settings_engine_agent.now_tz", return_value=now_value):
            command_id = _run_single_settings_cycle(config, shared, tz=timezone.utc)

        self.assertEqual(command_id, status["id"])
        with shared["lock"]:
            self.assertFalse(bool(shared["posting_runtime"]["policy_enabled"]))
            self.assertEqual(shared["posting_runtime"]["state"], "disabled")
            final_status = dict(shared["settings_command_status_by_id"][status["id"]])
            self.assertEqual(final_status["state"], "succeeded")
            self.assertEqual(shared["settings_engine_status"]["last_finished_command"]["id"], status["id"])
            self.assertEqual(shared["api_connection_runtime"]["posting_health"]["state"], "disabled")


if __name__ == "__main__":
    unittest.main()
