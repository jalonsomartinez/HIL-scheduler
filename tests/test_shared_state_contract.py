import queue
import threading
import unittest

import pandas as pd

from config_loader import load_config
from hil_scheduler import build_initial_shared_data


class SharedStateContractTests(unittest.TestCase):
    def test_build_initial_shared_data_contains_required_runtime_keys(self):
        config = load_config("config.yaml")
        shared_data = build_initial_shared_data(config)
        plant_ids = tuple(config.get("PLANT_IDS", ("lib", "vrfb")))

        required_keys = {
            "session_logs",
            "log_lock",
            "manual_schedule_df_by_plant",
            "manual_schedule_draft_series_df_by_key",
            "manual_schedule_series_df_by_key",
            "manual_schedule_merge_enabled_by_key",
            "manual_series_runtime_state_by_key",
            "api_day_ahead_schedule_df_by_plant",
            "api_mfrr_schedule_df_by_plant",
            "api_schedule_df_by_plant",
            "transport_mode",
            "scheduler_running_by_plant",
            "plant_transition_by_plant",
            "measurements_filename_by_plant",
            "current_file_path_by_plant",
            "current_file_df_by_plant",
            "pending_rows_by_file",
            "twin_measurements_filename",
            "twin_current_file_path",
            "twin_current_file_df",
            "pending_twin_rows_by_file",
            "twin_nobat_measurements_filename",
            "twin_nobat_current_file_path",
            "twin_nobat_current_file_df",
            "pending_twin_nobat_rows_by_file",
            "measurements_df",
            "measurement_post_status",
            "local_emulator_soc_seed_request_by_plant",
            "local_emulator_soc_seed_result_by_plant",
            "posting_runtime",
            "api_password",
            "api_connection_runtime",
            "data_fetcher_status",
            "transport_switching",
            "control_command_queue",
            "control_command_status_by_id",
            "control_command_history_ids",
            "control_command_active_id",
            "control_command_next_id",
            "plant_observed_state_by_plant",
            "plant_operating_state_by_plant",
            "reactive_control_mode_by_plant",
            "reactive_control_mode_runtime_by_plant",
            "dispatch_write_status_by_plant",
            "control_engine_status",
            "settings_command_queue",
            "settings_command_status_by_id",
            "settings_command_history_ids",
            "settings_command_active_id",
            "settings_command_next_id",
            "settings_engine_status",
            "grid_map_runtime",
            "lock",
            "shutdown_event",
            "log_file_path",
        }
        self.assertTrue(required_keys.issubset(shared_data.keys()))

        self.assertIsInstance(shared_data["lock"], type(threading.Lock()))
        self.assertIsInstance(shared_data["shutdown_event"], threading.Event)
        self.assertIsInstance(shared_data["control_command_queue"], queue.Queue)
        self.assertEqual(set(shared_data["manual_schedule_df_by_plant"].keys()), set(plant_ids))
        expected_manual_keys = {"lib_p", "lib_q", "lib_v", "vrfb_p", "vrfb_q", "vrfb_v"}
        self.assertEqual(set(shared_data["manual_schedule_draft_series_df_by_key"].keys()), expected_manual_keys)
        self.assertEqual(set(shared_data["manual_schedule_series_df_by_key"].keys()), expected_manual_keys)
        self.assertEqual(set(shared_data["manual_schedule_merge_enabled_by_key"].keys()), expected_manual_keys)
        self.assertEqual(set(shared_data["manual_series_runtime_state_by_key"].keys()), expected_manual_keys)
        self.assertEqual(set(shared_data["api_day_ahead_schedule_df_by_plant"].keys()), set(plant_ids))
        self.assertEqual(set(shared_data["api_mfrr_schedule_df_by_plant"].keys()), set(plant_ids))
        self.assertEqual(set(shared_data["api_schedule_df_by_plant"].keys()), set(plant_ids))
        self.assertEqual(set(shared_data["scheduler_running_by_plant"].keys()), set(plant_ids))
        self.assertEqual(set(shared_data["measurement_post_status"].keys()), set(plant_ids))
        self.assertEqual(set(shared_data["local_emulator_soc_seed_request_by_plant"].keys()), set(plant_ids))
        self.assertEqual(set(shared_data["local_emulator_soc_seed_result_by_plant"].keys()), set(plant_ids))
        self.assertEqual(set(shared_data["plant_observed_state_by_plant"].keys()), set(plant_ids))
        self.assertEqual(set(shared_data["plant_operating_state_by_plant"].keys()), set(plant_ids))
        self.assertEqual(set(shared_data["reactive_control_mode_by_plant"].keys()), set(plant_ids))
        self.assertEqual(set(shared_data["reactive_control_mode_runtime_by_plant"].keys()), set(plant_ids))
        self.assertEqual(set(shared_data["dispatch_write_status_by_plant"].keys()), set(plant_ids))
        self.assertIsInstance(shared_data["control_engine_status"], dict)
        self.assertIsInstance(shared_data["settings_command_queue"], queue.Queue)
        self.assertIsInstance(shared_data["settings_engine_status"], dict)
        self.assertIsInstance(shared_data["grid_map_runtime"], dict)
        self.assertIsInstance(shared_data["api_connection_runtime"], dict)
        self.assertIsInstance(shared_data["posting_runtime"], dict)
        grid_map_runtime = dict(shared_data["grid_map_runtime"])
        self.assertTrue(
            {
                "state",
                "poll_period_s",
                "topology_ready",
                "topology_error",
                "topology_cache",
                "topology_cache_meta",
                "last_run_at",
                "last_success_at",
                "last_error",
                "requested_timestamp_local",
                "selected_timestamp_local",
                "selected_timestamp_utc",
                "used_previous_hour_fallback",
                "input_source",
                "input_measured_at",
                "battery_input_p_kw",
                "battery_input_q_kvar",
                "battery_input_p_mw",
                "battery_input_q_mvar",
                "summary",
                "dynamic_payload",
                "initial_figure",
                "trace_index_meta",
                "topology_revision",
                "dynamic_revision",
                "coordinate_mode",
                "source_crs",
                "target_crs",
                "map_background_mode",
                "map_background_enabled",
                "map_background_reason",
                "stale",
                "scenario_results",
            }.issubset(grid_map_runtime.keys())
        )
        fetcher_status = dict(shared_data.get("data_fetcher_status", {}) or {})
        mfrr_poll = dict(fetcher_status.get("mfrr_poll", {}) or {})
        self.assertTrue(
            {
                "last_attempt_at",
                "last_success_at",
                "last_result",
                "last_error",
                "last_points_lib",
                "next_scheduled_at",
                "poll_period_s",
            }.issubset(mfrr_poll.keys())
        )
        self.assertEqual(str(mfrr_poll.get("last_result")), "never")
        self.assertEqual(int(mfrr_poll.get("last_points_lib", 0)), 0)
        self.assertEqual(float(grid_map_runtime.get("poll_period_s")), 10.0)
        self.assertEqual(int(grid_map_runtime.get("dynamic_revision", 0)), 0)
        self.assertEqual(str(grid_map_runtime.get("coordinate_mode")), "schematic")
        self.assertEqual(str(grid_map_runtime.get("map_background_mode")), "none")
        self.assertFalse(bool(grid_map_runtime.get("map_background_enabled", False)))
        self.assertTrue(bool(grid_map_runtime.get("stale", True)))
        self.assertTrue(
            all(result.get("status") == "idle" for result in shared_data["local_emulator_soc_seed_result_by_plant"].values())
        )
        self.assertTrue(all(state.get("stale") is True for state in shared_data["plant_observed_state_by_plant"].values()))
        self.assertTrue(all(state in {"unknown"} for state in shared_data["plant_operating_state_by_plant"].values()))
        self.assertTrue(all("read_status" in state for state in shared_data["plant_observed_state_by_plant"].values()))
        self.assertTrue(all("consecutive_failures" in state for state in shared_data["plant_observed_state_by_plant"].values()))
        self.assertTrue(all("start_command_state" in state for state in shared_data["plant_observed_state_by_plant"].values()))
        self.assertTrue(all("stop_command_state" in state for state in shared_data["plant_observed_state_by_plant"].values()))
        self.assertTrue(all("q_control_mode_state" in state for state in shared_data["plant_observed_state_by_plant"].values()))
        self.assertTrue(all(mode == 1 for mode in shared_data["reactive_control_mode_by_plant"].values()))
        self.assertTrue(
            all(int((entry or {}).get("selected_mode", 0)) == 1 for entry in shared_data["reactive_control_mode_runtime_by_plant"].values())
        )
        self.assertTrue(
            all(
                {
                    "sending_enabled",
                    "last_attempt_at",
                    "last_attempt_p_kw",
                    "last_attempt_q_kvar",
                    "last_attempt_source",
                    "last_attempt_status",
                    "last_success_at",
                    "last_success_p_kw",
                    "last_success_q_kvar",
                    "last_success_source",
                    "last_error",
                    "last_scheduler_context",
                }.issubset(entry.keys())
                for entry in shared_data["dispatch_write_status_by_plant"].values()
            )
        )
        self.assertTrue(
            {
                "alive",
                "last_loop_start",
                "last_loop_end",
                "last_observed_refresh",
                "last_exception",
                "active_command_id",
                "active_command_kind",
                "active_command_started_at",
                "last_finished_command",
                "queue_depth",
                "queued_count",
                "running_count",
                "failed_recent_count",
            }.issubset(shared_data["control_engine_status"].keys())
        )
        self.assertTrue(
            {
                "alive",
                "last_loop_start",
                "last_loop_end",
                "last_exception",
                "active_command_id",
                "active_command_kind",
                "active_command_started_at",
                "last_finished_command",
                "queue_depth",
                "queued_count",
                "running_count",
                "failed_recent_count",
            }.issubset(shared_data["settings_engine_status"].keys())
        )
        api_runtime = dict(shared_data["api_connection_runtime"])
        self.assertTrue(
            {
                "state",
                "connected",
                "desired_state",
                "last_command_id",
                "last_error",
                "last_updated",
                "last_success",
                "last_probe",
                "disconnect_reason",
                "fetch_health",
                "posting_health",
            }.issubset(api_runtime.keys())
        )
        self.assertTrue({"state", "last_success", "last_error", "last_attempt"}.issubset(api_runtime["fetch_health"].keys()))
        self.assertTrue({"state", "last_success", "last_error", "last_attempt"}.issubset(api_runtime["posting_health"].keys()))
        self.assertIsInstance(shared_data["measurements_df"], pd.DataFrame)

    def test_build_initial_shared_data_normalizes_invalid_startup_values(self):
        config = {
            "PLANT_IDS": ("lib", "vrfb"),
            "STARTUP_SCHEDULE_SOURCE": "bad-source",
            "STARTUP_TRANSPORT_MODE": "bad-mode",
        }
        shared_data = build_initial_shared_data(config)
        self.assertEqual(shared_data["transport_mode"], "local")


if __name__ == "__main__":
    unittest.main()
