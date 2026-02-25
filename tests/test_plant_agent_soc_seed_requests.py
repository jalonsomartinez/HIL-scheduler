import threading
import time
import unittest

import pandas as pd

from config_loader import load_config
from plant_agent import plant_agent
from tests.test_local_runtime_smoke import _FakeModbusRegistry, _FakeModbusServer


def _empty_df_by_plant(plant_ids):
    return {plant_id: pd.DataFrame() for plant_id in plant_ids}


def _build_shared_data(config):
    plant_ids = tuple(config.get("PLANT_IDS", ("lib", "vrfb")))
    return {
        "manual_schedule_df_by_plant": _empty_df_by_plant(plant_ids),
        "api_schedule_df_by_plant": _empty_df_by_plant(plant_ids),
        "transport_mode": "local",
        "scheduler_running_by_plant": {plant_id: False for plant_id in plant_ids},
        "plant_transition_by_plant": {plant_id: "stopped" for plant_id in plant_ids},
        "measurements_filename_by_plant": {plant_id: None for plant_id in plant_ids},
        "current_file_path_by_plant": {plant_id: None for plant_id in plant_ids},
        "current_file_df_by_plant": _empty_df_by_plant(plant_ids),
        "pending_rows_by_file": {},
        "measurements_df": pd.DataFrame(),
        "measurement_post_status": {plant_id: {} for plant_id in plant_ids},
        "local_emulator_soc_seed_request_by_plant": {plant_id: None for plant_id in plant_ids},
        "local_emulator_soc_seed_result_by_plant": {
            plant_id: {"request_id": None, "status": "idle", "soc_pu": None, "message": None} for plant_id in plant_ids
        },
        "lock": threading.Lock(),
        "shutdown_event": threading.Event(),
    }


def _wait_for_seed_result(shared_data, plant_id, request_id, timeout_s=2.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with shared_data["lock"]:
            result = dict(shared_data["local_emulator_soc_seed_result_by_plant"].get(plant_id, {}))
        if result.get("request_id") == request_id:
            return result
        time.sleep(0.05)
    return None


class PlantAgentSocSeedRequestTests(unittest.TestCase):
    def setUp(self):
        _FakeModbusRegistry.clear()

    def test_applies_seed_request_when_plant_disabled(self):
        config = load_config("config.yaml")
        config["PLANT_PERIOD_S"] = 0.05
        config["PLANTS"]["lib"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["lib"]["modbus"]["local"]["port"] = 5120
        config["PLANTS"]["vrfb"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["vrfb"]["modbus"]["local"]["port"] = 5121
        shared_data = _build_shared_data(config)

        thread = None
        try:
            from unittest.mock import patch

            with patch("plant_agent.ModbusServer", _FakeModbusServer):
                thread = threading.Thread(target=plant_agent, args=(config, shared_data), daemon=True)
                thread.start()
                time.sleep(0.2)

                request_id = 101
                with shared_data["lock"]:
                    shared_data["local_emulator_soc_seed_request_by_plant"]["lib"] = {
                        "request_id": request_id,
                        "soc_pu": 0.77,
                        "source": "test",
                    }

                result = _wait_for_seed_result(shared_data, "lib", request_id)
                self.assertIsNotNone(result)
                self.assertEqual(result["status"], "applied")
                self.assertAlmostEqual(float(result["soc_pu"]), 0.77, places=6)
        finally:
            shared_data["shutdown_event"].set()
            if thread is not None:
                thread.join(timeout=2)

    def test_skips_seed_request_when_plant_enabled(self):
        config = load_config("config.yaml")
        config["PLANT_PERIOD_S"] = 0.05
        config["PLANTS"]["lib"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["lib"]["modbus"]["local"]["port"] = 5130
        config["PLANTS"]["vrfb"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["vrfb"]["modbus"]["local"]["port"] = 5131
        shared_data = _build_shared_data(config)

        thread = None
        try:
            from unittest.mock import patch

            with patch("plant_agent.ModbusServer", _FakeModbusServer):
                thread = threading.Thread(target=plant_agent, args=(config, shared_data), daemon=True)
                thread.start()
                time.sleep(0.2)

                lib_server = _FakeModbusRegistry.get("127.0.0.1", 5130)
                self.assertIsNotNone(lib_server)
                enable_reg = int(config["PLANTS"]["lib"]["modbus"]["local"]["points"]["enable"]["address"])
                lib_server.data_bank.set_holding_registers(enable_reg, [1])

                request_id = 102
                with shared_data["lock"]:
                    shared_data["local_emulator_soc_seed_request_by_plant"]["lib"] = {
                        "request_id": request_id,
                        "soc_pu": 0.12,
                        "source": "test",
                    }

                result = _wait_for_seed_result(shared_data, "lib", request_id)
                self.assertIsNotNone(result)
                self.assertEqual(result["status"], "skipped")
                self.assertIn("enabled", str(result.get("message", "")))
        finally:
            shared_data["shutdown_event"].set()
            if thread is not None:
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
