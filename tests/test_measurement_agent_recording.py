import os
import threading
import tempfile
import unittest
from contextlib import chdir
from unittest.mock import patch

import pandas as pd

from measurement_agent import measurement_agent
from measurement_storage import MEASUREMENT_COLUMNS


def _build_shared_data(lib_file_path):
    return {
        "lock": threading.Lock(),
        "shutdown_event": threading.Event(),
        "transport_mode": "local",
        "active_schedule_source": "manual",
        "api_password": None,
        "measurements_filename_by_plant": {"lib": lib_file_path, "vrfb": None},
        "current_file_path_by_plant": {"lib": None, "vrfb": None},
        "current_file_df_by_plant": {"lib": pd.DataFrame(), "vrfb": pd.DataFrame()},
        "pending_rows_by_file": {},
        "measurements_df": pd.DataFrame(),
        "measurement_post_status": {},
    }


def _build_config():
    return {
        "TIMEZONE_NAME": "Europe/Madrid",
        "PLANT_IDS": ("lib", "vrfb"),
        "PLANTS": {
            "lib": {"name": "LIB"},
            "vrfb": {"name": "VRFB"},
        },
        "MEASUREMENT_PERIOD_S": 0.2,
        "MEASUREMENTS_WRITE_PERIOD_S": 0.2,
        "ISTENTORE_POST_MEASUREMENTS_IN_API_MODE": False,
        "ISTENTORE_MEASUREMENT_POST_PERIOD_S": 60,
        "ISTENTORE_MEASUREMENT_POST_QUEUE_MAXLEN": 10,
        "ISTENTORE_MEASUREMENT_POST_RETRY_INITIAL_S": 1,
        "ISTENTORE_MEASUREMENT_POST_RETRY_MAX_S": 2,
    }


class MeasurementAgentRecordingTests(unittest.TestCase):
    def test_record_start_path_does_not_crash_and_appends_boundary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with chdir(tmpdir):
                os.makedirs("data", exist_ok=True)
                lib_file_path = os.path.join("data", "20260221_lib.csv")

                initial_row = {
                    "timestamp": "2026-02-21T14:04:49+01:00",
                    "p_setpoint_kw": 10.0,
                    "battery_active_power_kw": 10.0,
                    "q_setpoint_kvar": 0.0,
                    "battery_reactive_power_kvar": 0.0,
                    "soc_pu": 0.5,
                    "p_poi_kw": 10.0,
                    "q_poi_kvar": 0.0,
                    "v_poi_pu": 1.0,
                }
                pd.DataFrame([initial_row], columns=MEASUREMENT_COLUMNS).to_csv(lib_file_path, index=False)
                initial_len = len(pd.read_csv(lib_file_path))

                shared_data = _build_shared_data(lib_file_path=lib_file_path)
                config = _build_config()

                # End the agent loop shortly after startup; this test targets record-start stability.
                stop_timer = threading.Timer(0.35, shared_data["shutdown_event"].set)
                stop_timer.start()
                try:
                    with patch(
                        "measurement_agent.sampling_get_transport_endpoint",
                        return_value={
                            "host": "localhost",
                            "port": 5020,
                            "p_setpoint_reg": 1,
                            "p_battery_reg": 2,
                            "q_setpoint_reg": 3,
                            "q_battery_reg": 4,
                            "soc_reg": 5,
                            "p_poi_reg": 6,
                            "q_poi_reg": 7,
                            "v_poi_reg": 8,
                        },
                    ), patch(
                        "measurement_agent.sampling_ensure_client",
                        return_value=None,
                    ), patch(
                        "measurement_agent.sampling_take_measurement",
                        return_value=None,
                    ):
                        measurement_agent(config, shared_data)
                finally:
                    stop_timer.cancel()

                final_df = pd.read_csv(lib_file_path)
                self.assertGreater(len(final_df), initial_len)


if __name__ == "__main__":
    unittest.main()
