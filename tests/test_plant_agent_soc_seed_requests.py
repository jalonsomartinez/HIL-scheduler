import threading
import time
import unittest

import pandas as pd

import plant_agent as plant_agent_module
from config_loader import load_config
from modbus.codec import read_point_internal, write_point_internal
from plant_agent import plant_agent
from tests.test_local_runtime_smoke import _FakeModbusClient, _FakeModbusRegistry, _FakeModbusServer


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

    def test_startup_initial_soc_uses_latest_persisted_soc_when_available(self):
        config = load_config("config.yaml")
        config["PLANT_PERIOD_S"] = 0.05
        config["PLANTS"]["lib"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["lib"]["modbus"]["local"]["port"] = 5140
        config["PLANTS"]["vrfb"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["vrfb"]["modbus"]["local"]["port"] = 5141
        shared_data = _build_shared_data(config)

        thread = None
        try:
            from unittest.mock import patch

            with patch("plant_agent.ModbusServer", _FakeModbusServer), patch(
                "plant_agent.resolve_startup_soc_seed",
                return_value={
                    "soc_pu": 0.77,
                    "source": "disk",
                    "file_path": "data/20990101_lib.csv",
                    "timestamp": pd.Timestamp("2099-01-01T00:00:00+01:00"),
                },
            ):
                thread = threading.Thread(target=plant_agent, args=(config, shared_data), daemon=True)
                thread.start()
                time.sleep(0.2)

                client = _FakeModbusClient("127.0.0.1", 5140)
                self.assertTrue(client.open())
                soc_pu = read_point_internal(client, config["PLANTS"]["lib"]["modbus"]["local"], "soc")
                self.assertIsNotNone(soc_pu)
                self.assertAlmostEqual(float(soc_pu), 0.77, places=4)
        finally:
            shared_data["shutdown_event"].set()
            if thread is not None:
                thread.join(timeout=2)

    def test_startup_initial_soc_falls_back_when_no_persisted_soc(self):
        config = load_config("config.yaml")
        config["PLANT_PERIOD_S"] = 0.05
        config["STARTUP_INITIAL_SOC_PU"] = 0.63
        config["PLANTS"]["lib"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["lib"]["modbus"]["local"]["port"] = 5150
        config["PLANTS"]["vrfb"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["vrfb"]["modbus"]["local"]["port"] = 5151
        shared_data = _build_shared_data(config)

        thread = None
        try:
            from unittest.mock import patch

            with patch("plant_agent.ModbusServer", _FakeModbusServer), patch(
                "plant_agent.resolve_startup_soc_seed",
                return_value={"soc_pu": 0.63, "source": "startup_fallback", "file_path": None, "timestamp": None},
            ):
                thread = threading.Thread(target=plant_agent, args=(config, shared_data), daemon=True)
                thread.start()
                time.sleep(0.2)

                client = _FakeModbusClient("127.0.0.1", 5150)
                self.assertTrue(client.open())
                soc_pu = read_point_internal(client, config["PLANTS"]["lib"]["modbus"]["local"], "soc")
                self.assertIsNotNone(soc_pu)
                self.assertAlmostEqual(float(soc_pu), 0.63, places=4)
        finally:
            shared_data["shutdown_event"].set()
            if thread is not None:
                thread.join(timeout=2)

    def test_seed_request_applies_even_when_soc_register_is_not_configured(self):
        config = load_config("config.yaml")
        config["PLANT_PERIOD_S"] = 0.05
        config["PLANTS"]["lib"]["modbus"]["local"]["points"].pop("soc", None)
        config["PLANTS"]["lib"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["lib"]["modbus"]["local"]["port"] = 5152
        config["PLANTS"]["vrfb"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["vrfb"]["modbus"]["local"]["port"] = 5153
        shared_data = _build_shared_data(config)

        thread = None
        try:
            from unittest.mock import patch

            with patch("plant_agent.ModbusServer", _FakeModbusServer), patch(
                "plant_agent.resolve_startup_soc_seed",
                return_value={"soc_pu": 0.41, "source": "startup_fallback", "file_path": None},
            ):
                thread = threading.Thread(target=plant_agent, args=(config, shared_data), daemon=True)
                thread.start()
                time.sleep(0.2)

                request_id = 103
                with shared_data["lock"]:
                    shared_data["local_emulator_soc_seed_request_by_plant"]["lib"] = {
                        "request_id": request_id,
                        "soc_pu": 0.73,
                        "source": "test",
                    }

                result = _wait_for_seed_result(shared_data, "lib", request_id)
                self.assertIsNotNone(result)
                self.assertEqual(result["status"], "applied")
                self.assertAlmostEqual(float(result["soc_pu"]), 0.73, places=6)
        finally:
            shared_data["shutdown_event"].set()
            if thread is not None:
                thread.join(timeout=2)


class PlantAgentVoltageMirroringTests(unittest.TestCase):
    def setUp(self):
        _FakeModbusRegistry.clear()

    def test_local_voltage_mirrors_v_poi_write_when_configured(self):
        config = load_config("config.yaml")
        config["PLANT_PERIOD_S"] = 0.05
        config["PLANTS"]["lib"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["lib"]["modbus"]["local"]["port"] = 5160
        config["PLANTS"]["vrfb"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["vrfb"]["modbus"]["local"]["port"] = 5161
        config["PLANTS"]["vrfb"]["modbus"]["local"]["points"]["v_poi_write"] = {
            "name": "v_poi_write",
            "address": 30,
            "format": "uint16",
            "word_count": 1,
            "byte_count": 2,
            "access": "rw",
            "unit": "v",
            "eng_per_count": 1.0,
        }
        shared_data = _build_shared_data(config)

        thread = None
        try:
            from unittest.mock import patch

            with patch("plant_agent.ModbusServer", _FakeModbusServer), patch(
                "plant_agent.resolve_startup_soc_seed",
                return_value={"soc_pu": 0.5, "source": "startup_fallback", "file_path": None, "timestamp": None},
            ):
                thread = threading.Thread(target=plant_agent, args=(config, shared_data), daemon=True)
                thread.start()
                time.sleep(0.2)

                client = _FakeModbusClient("127.0.0.1", 5161)
                self.assertTrue(client.open())
                vrfb_endpoint = config["PLANTS"]["vrfb"]["modbus"]["local"]
                self.assertTrue(write_point_internal(client, vrfb_endpoint, "v_poi_write", 19.93))

                time.sleep(0.15)

                voltage_kv = read_point_internal(client, vrfb_endpoint, "v_poi")
                self.assertIsNotNone(voltage_kv)
                self.assertAlmostEqual(float(voltage_kv), 19.93, places=3)
        finally:
            shared_data["shutdown_event"].set()
            if thread is not None:
                thread.join(timeout=2)

    def test_local_voltage_defaults_to_rated_value_without_v_poi_write(self):
        config = load_config("config.yaml")
        config["PLANT_PERIOD_S"] = 0.05
        config["PLANTS"]["lib"]["modbus"]["local"]["points"].pop("v_poi_write", None)
        config["PLANTS"]["lib"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["lib"]["modbus"]["local"]["port"] = 5170
        config["PLANTS"]["vrfb"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["vrfb"]["modbus"]["local"]["port"] = 5171
        shared_data = _build_shared_data(config)

        thread = None
        try:
            from unittest.mock import patch

            with patch("plant_agent.ModbusServer", _FakeModbusServer), patch(
                "plant_agent.resolve_startup_soc_seed",
                return_value={"soc_pu": 0.5, "source": "startup_fallback", "file_path": None, "timestamp": None},
            ):
                thread = threading.Thread(target=plant_agent, args=(config, shared_data), daemon=True)
                thread.start()
                time.sleep(0.2)

                client = _FakeModbusClient("127.0.0.1", 5170)
                self.assertTrue(client.open())
                lib_endpoint = config["PLANTS"]["lib"]["modbus"]["local"]
                voltage_kv = read_point_internal(client, lib_endpoint, "v_poi")
                self.assertIsNotNone(voltage_kv)
                self.assertAlmostEqual(float(voltage_kv), 20.0, places=3)
        finally:
            shared_data["shutdown_event"].set()
            if thread is not None:
                thread.join(timeout=2)


class PlantAgentEmulatorStartupModeTests(unittest.TestCase):
    def setUp(self):
        _FakeModbusRegistry.clear()

    def test_non_loopback_local_hosts_skip_emulator_startup_without_crashing(self):
        config = load_config("config_prod.yaml")
        config["PLANT_PERIOD_S"] = 0.05
        config["PLANTS"]["lib"]["modbus"]["local"]["host"] = "10.117.133.21"
        config["PLANTS"]["lib"]["modbus"]["local"]["port"] = 502
        config["PLANTS"]["vrfb"]["modbus"]["local"]["host"] = "10.117.133.12"
        config["PLANTS"]["vrfb"]["modbus"]["local"]["port"] = 502
        shared_data = _build_shared_data(config)

        thread = None
        try:
            from unittest.mock import patch

            with patch("plant_agent.ModbusServer", _FakeModbusServer):
                thread = threading.Thread(target=plant_agent, args=(config, shared_data), daemon=True)
                thread.start()
                time.sleep(0.2)

                self.assertTrue(thread.is_alive())
                self.assertIsNone(_FakeModbusRegistry.get("10.117.133.21", 502))
                self.assertIsNone(_FakeModbusRegistry.get("10.117.133.12", 502))
        finally:
            shared_data["shutdown_event"].set()
            if thread is not None:
                thread.join(timeout=2)

    def test_mixed_local_mode_starts_only_loopback_backed_emulator(self):
        config = load_config("config_prod.yaml")
        config["PLANT_PERIOD_S"] = 0.05
        config["PLANTS"]["lib"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["lib"]["modbus"]["local"]["port"] = 5190
        config["PLANTS"]["vrfb"]["modbus"]["local"]["host"] = "10.117.133.12"
        config["PLANTS"]["vrfb"]["modbus"]["local"]["port"] = 502
        shared_data = _build_shared_data(config)

        thread = None
        try:
            from unittest.mock import patch

            with patch("plant_agent.ModbusServer", _FakeModbusServer), patch(
                "plant_agent.resolve_startup_soc_seed",
                return_value={"soc_pu": 0.5, "source": "startup_fallback", "file_path": None, "timestamp": None},
            ):
                thread = threading.Thread(target=plant_agent, args=(config, shared_data), daemon=True)
                thread.start()
                time.sleep(0.2)

                self.assertIsNotNone(_FakeModbusRegistry.get("127.0.0.1", 5190))
                self.assertIsNone(_FakeModbusRegistry.get("10.117.133.12", 502))
        finally:
            shared_data["shutdown_event"].set()
            if thread is not None:
                thread.join(timeout=2)


class PlantAgentPerPhaseSetpointTests(unittest.TestCase):
    def setUp(self):
        _FakeModbusRegistry.clear()

    def _build_per_phase_local_config(self):
        config = load_config("config_test.yaml")
        config["PLANT_PERIOD_S"] = 0.05
        config["PLANTS"]["lib"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["lib"]["modbus"]["local"]["port"] = 5180
        config["PLANTS"]["vrfb"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["vrfb"]["modbus"]["local"]["port"] = 5181

        points = config["PLANTS"]["vrfb"]["modbus"]["local"]["points"]
        points.pop("p_setpoint", None)
        points.pop("q_setpoint", None)
        points["p_u_setpoint"] = {"name": "p_u_setpoint", "address": 101, "format": "float32", "word_count": 2, "byte_count": 4, "access": "rw", "unit": "w", "eng_per_count": 1.0}
        points["p_v_setpoint"] = {"name": "p_v_setpoint", "address": 103, "format": "float32", "word_count": 2, "byte_count": 4, "access": "rw", "unit": "w", "eng_per_count": 1.0}
        points["p_w_setpoint"] = {"name": "p_w_setpoint", "address": 105, "format": "float32", "word_count": 2, "byte_count": 4, "access": "rw", "unit": "w", "eng_per_count": 1.0}
        points["q_u_setpoint"] = {"name": "q_u_setpoint", "address": 107, "format": "float32", "word_count": 2, "byte_count": 4, "access": "rw", "unit": "var", "eng_per_count": 1.0}
        points["q_v_setpoint"] = {"name": "q_v_setpoint", "address": 109, "format": "float32", "word_count": 2, "byte_count": 4, "access": "rw", "unit": "var", "eng_per_count": 1.0}
        points["q_w_setpoint"] = {"name": "q_w_setpoint", "address": 111, "format": "float32", "word_count": 2, "byte_count": 4, "access": "rw", "unit": "var", "eng_per_count": 1.0}
        return config

    def test_per_phase_local_setpoints_drive_aggregate_telemetry(self):
        config = self._build_per_phase_local_config()
        shared_data = _build_shared_data(config)

        thread = None
        original_server_cls = plant_agent_module.ModbusServer
        original_seed_resolver = plant_agent_module.resolve_startup_soc_seed
        try:
            plant_agent_module.ModbusServer = _FakeModbusServer
            plant_agent_module.resolve_startup_soc_seed = (
                lambda *args, **kwargs: {"soc_pu": 0.5, "source": "startup_fallback", "file_path": None, "timestamp": None}
            )
            thread = threading.Thread(target=plant_agent, args=(config, shared_data), daemon=True)
            thread.start()
            time.sleep(0.3)

            client = _FakeModbusClient("127.0.0.1", 5181)
            self.assertTrue(client.open())
            vrfb_endpoint = config["PLANTS"]["vrfb"]["modbus"]["local"]
            self.assertTrue(write_point_internal(client, vrfb_endpoint, "enable", 1))
            self.assertTrue(write_point_internal(client, vrfb_endpoint, "p_u_setpoint", 4.0))
            self.assertTrue(write_point_internal(client, vrfb_endpoint, "p_v_setpoint", 5.0))
            self.assertTrue(write_point_internal(client, vrfb_endpoint, "p_w_setpoint", 7.0))
            self.assertTrue(write_point_internal(client, vrfb_endpoint, "q_u_setpoint", 0.25))
            self.assertTrue(write_point_internal(client, vrfb_endpoint, "q_v_setpoint", 0.5))
            self.assertTrue(write_point_internal(client, vrfb_endpoint, "q_w_setpoint", 0.75))

            time.sleep(0.2)

            p_poi_kw = read_point_internal(client, vrfb_endpoint, "p_poi")
            q_poi_kvar = read_point_internal(client, vrfb_endpoint, "q_poi")
            p_battery_kw = read_point_internal(client, vrfb_endpoint, "p_battery")
            q_battery_kvar = read_point_internal(client, vrfb_endpoint, "q_battery")
            self.assertAlmostEqual(float(p_poi_kw), 16.0, places=6)
            self.assertAlmostEqual(float(q_poi_kvar), 1.5, places=6)
            self.assertAlmostEqual(float(p_battery_kw), 16.0, places=6)
            self.assertAlmostEqual(float(q_battery_kvar), 1.5, places=6)
        finally:
            shared_data["shutdown_event"].set()
            if thread is not None:
                thread.join(timeout=2)
            plant_agent_module.ModbusServer = original_server_cls
            plant_agent_module.resolve_startup_soc_seed = original_seed_resolver

    def test_per_phase_local_seed_request_still_applies(self):
        config = self._build_per_phase_local_config()
        shared_data = _build_shared_data(config)

        thread = None
        original_server_cls = plant_agent_module.ModbusServer
        original_seed_resolver = plant_agent_module.resolve_startup_soc_seed
        try:
            plant_agent_module.ModbusServer = _FakeModbusServer
            plant_agent_module.resolve_startup_soc_seed = (
                lambda *args, **kwargs: {"soc_pu": 0.5, "source": "startup_fallback", "file_path": None, "timestamp": None}
            )
            thread = threading.Thread(target=plant_agent, args=(config, shared_data), daemon=True)
            thread.start()
            time.sleep(0.3)

            request_id = 104
            with shared_data["lock"]:
                shared_data["local_emulator_soc_seed_request_by_plant"]["vrfb"] = {
                    "request_id": request_id,
                    "soc_pu": 0.66,
                    "source": "test",
                }

            result = _wait_for_seed_result(shared_data, "vrfb", request_id)
            self.assertIsNotNone(result)
            self.assertEqual(result["status"], "applied")
            self.assertAlmostEqual(float(result["soc_pu"]), 0.66, places=6)
        finally:
            shared_data["shutdown_event"].set()
            if thread is not None:
                thread.join(timeout=2)
            plant_agent_module.ModbusServer = original_server_cls
            plant_agent_module.resolve_startup_soc_seed = original_seed_resolver


if __name__ == "__main__":
    unittest.main()
