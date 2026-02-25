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
            "manual_schedule_series_df_by_key",
            "manual_schedule_merge_enabled_by_key",
            "api_schedule_df_by_plant",
            "active_schedule_source",
            "transport_mode",
            "scheduler_running_by_plant",
            "plant_transition_by_plant",
            "measurements_filename_by_plant",
            "current_file_path_by_plant",
            "current_file_df_by_plant",
            "pending_rows_by_file",
            "measurements_df",
            "measurement_post_status",
            "local_emulator_soc_seed_request_by_plant",
            "local_emulator_soc_seed_result_by_plant",
            "measurement_posting_enabled",
            "api_password",
            "data_fetcher_status",
            "schedule_switching",
            "transport_switching",
            "lock",
            "shutdown_event",
            "log_file_path",
        }
        self.assertTrue(required_keys.issubset(shared_data.keys()))

        self.assertIsInstance(shared_data["lock"], type(threading.Lock()))
        self.assertIsInstance(shared_data["shutdown_event"], threading.Event)
        self.assertEqual(set(shared_data["manual_schedule_df_by_plant"].keys()), set(plant_ids))
        self.assertEqual(set(shared_data["manual_schedule_series_df_by_key"].keys()), {"lib_p", "lib_q", "vrfb_p", "vrfb_q"})
        self.assertEqual(set(shared_data["manual_schedule_merge_enabled_by_key"].keys()), {"lib_p", "lib_q", "vrfb_p", "vrfb_q"})
        self.assertEqual(set(shared_data["api_schedule_df_by_plant"].keys()), set(plant_ids))
        self.assertEqual(set(shared_data["scheduler_running_by_plant"].keys()), set(plant_ids))
        self.assertEqual(set(shared_data["measurement_post_status"].keys()), set(plant_ids))
        self.assertEqual(set(shared_data["local_emulator_soc_seed_request_by_plant"].keys()), set(plant_ids))
        self.assertEqual(set(shared_data["local_emulator_soc_seed_result_by_plant"].keys()), set(plant_ids))
        self.assertTrue(
            all(result.get("status") == "idle" for result in shared_data["local_emulator_soc_seed_result_by_plant"].values())
        )
        self.assertIsInstance(shared_data["measurements_df"], pd.DataFrame)

    def test_build_initial_shared_data_normalizes_invalid_startup_values(self):
        config = {
            "PLANT_IDS": ("lib", "vrfb"),
            "STARTUP_SCHEDULE_SOURCE": "bad-source",
            "STARTUP_TRANSPORT_MODE": "bad-mode",
        }
        shared_data = build_initial_shared_data(config)
        self.assertEqual(shared_data["active_schedule_source"], "manual")
        self.assertEqual(shared_data["transport_mode"], "local")


if __name__ == "__main__":
    unittest.main()
