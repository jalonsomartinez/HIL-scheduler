import threading
import unittest

import pandas as pd

from control.flows import (
    perform_transport_switch,
    safe_stop_plant,
)


def _shared_data():
    return {
        "lock": threading.Lock(),
        "scheduler_running_by_plant": {"lib": True, "vrfb": True},
        "plant_transition_by_plant": {"lib": "running", "vrfb": "running"},
        "measurements_filename_by_plant": {"lib": "data/file.csv", "vrfb": "data/file2.csv"},
        "current_file_path_by_plant": {"lib": "data/file.csv", "vrfb": "data/file2.csv"},
        "current_file_df_by_plant": {"lib": pd.DataFrame([{"a": 1}]), "vrfb": pd.DataFrame([{"a": 2}])},
        "transport_mode": "local",
        "transport_switching": False,
    }


class DashboardControlFlowTests(unittest.TestCase):
    def test_safe_stop_plant_updates_gates_and_returns_status(self):
        shared_data = _shared_data()
        calls = []

        def _send_setpoints(plant_id, p_kw, q_kvar):
            calls.append(("setpoints", plant_id, p_kw, q_kvar))
            return True

        def _wait(plant_id, threshold_kw=1.0, timeout_s=30):
            calls.append(("wait", plant_id, threshold_kw, timeout_s))
            return True

        def _set_enable(plant_id, value):
            calls.append(("enable", plant_id, value))
            return True

        result = safe_stop_plant(
            shared_data,
            "lib",
            send_setpoints=_send_setpoints,
            wait_until_battery_power_below_threshold=_wait,
            set_enable=_set_enable,
            threshold_kw=2.0,
            timeout_s=5,
        )

        self.assertEqual(result, {"threshold_reached": True, "disable_ok": True})
        self.assertFalse(shared_data["scheduler_running_by_plant"]["lib"])
        self.assertEqual(shared_data["plant_transition_by_plant"]["lib"], "stopped")
        self.assertEqual(calls[0], ("setpoints", "lib", 0.0, 0.0))
        self.assertEqual(calls[1], ("wait", "lib", 2.0, 5))
        self.assertEqual(calls[2], ("enable", "lib", 0))

    def test_perform_transport_switch_resets_per_plant_runtime_state(self):
        shared_data = _shared_data()
        safe_stop_calls = []
        plant_ids = ("lib", "vrfb")

        def _safe_stop_all():
            safe_stop_calls.append("called")
            return {"lib": {"disable_ok": True}, "vrfb": {"disable_ok": True}}

        perform_transport_switch(shared_data, plant_ids, "remote", _safe_stop_all)

        self.assertEqual(shared_data["transport_mode"], "remote")
        self.assertFalse(shared_data["transport_switching"])
        self.assertEqual(len(safe_stop_calls), 1)
        for plant_id in plant_ids:
            self.assertFalse(shared_data["scheduler_running_by_plant"][plant_id])
            self.assertEqual(shared_data["plant_transition_by_plant"][plant_id], "stopped")
            self.assertIsNone(shared_data["measurements_filename_by_plant"][plant_id])
            self.assertIsNone(shared_data["current_file_path_by_plant"][plant_id])
            self.assertTrue(shared_data["current_file_df_by_plant"][plant_id].empty)

    def test_safe_stop_plant_timeout_path_propagates_result(self):
        shared_data = _shared_data()

        def _send_setpoints(plant_id, p_kw, q_kvar):
            return True

        def _wait(plant_id, threshold_kw=1.0, timeout_s=30):
            return False

        def _set_enable(plant_id, value):
            return False

        result = safe_stop_plant(
            shared_data,
            "lib",
            send_setpoints=_send_setpoints,
            wait_until_battery_power_below_threshold=_wait,
            set_enable=_set_enable,
        )

        self.assertEqual(result, {"threshold_reached": False, "disable_ok": False})
        self.assertEqual(shared_data["plant_transition_by_plant"]["lib"], "unknown")
        self.assertFalse(shared_data["scheduler_running_by_plant"]["lib"])


if __name__ == "__main__":
    unittest.main()
