import os
import tempfile
import unittest

import yaml

from config_loader import load_config


def _load_yaml(path):
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _write_temp_yaml(data):
    handle = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8")
    try:
        yaml.safe_dump(data, handle, sort_keys=False)
        return handle.name
    finally:
        handle.close()


class ConfigLoaderModbusPointsSchemaTests(unittest.TestCase):
    def test_accepts_full_per_phase_setpoint_endpoint(self):
        payload = _load_yaml("config.yaml")
        points = payload["plants"]["lib"]["modbus"]["local"]["points"]
        points.pop("p_setpoint", None)
        points.pop("q_setpoint", None)
        points["p_u_setpoint"] = {"address": 86, "format": "int16", "access": "rw", "unit": "kW", "eng_per_count": 0.1}
        points["p_v_setpoint"] = {"address": 87, "format": "int16", "access": "rw", "unit": "kW", "eng_per_count": 0.1}
        points["p_w_setpoint"] = {"address": 88, "format": "int16", "access": "rw", "unit": "kW", "eng_per_count": 0.1}
        points["q_u_setpoint"] = {"address": 89, "format": "int16", "access": "rw", "unit": "kvar", "eng_per_count": 0.1}
        points["q_v_setpoint"] = {"address": 90, "format": "int16", "access": "rw", "unit": "kvar", "eng_per_count": 0.1}
        points["q_w_setpoint"] = {"address": 91, "format": "int16", "access": "rw", "unit": "kvar", "eng_per_count": 0.1}
        path = _write_temp_yaml(payload)
        try:
            config = load_config(path)
        finally:
            os.unlink(path)

        point_map = config["PLANTS"]["lib"]["modbus"]["local"]["points"]
        self.assertNotIn("p_setpoint", point_map)
        self.assertNotIn("q_setpoint", point_map)
        self.assertIn("p_u_setpoint", point_map)
        self.assertIn("q_w_setpoint", point_map)

    def test_load_config_normalizes_endpoint_ordering_and_point_specs(self):
        payload = _load_yaml("config.yaml")
        lib_remote_payload = payload["plants"]["lib"]["modbus"]["remote"]
        lib_remote_payload["byte_order"] = "BIG"
        lib_remote_payload["word_order"] = "MSW_FIRST"
        p_setpoint_payload = lib_remote_payload["points"]["p_setpoint"]
        p_setpoint_payload["format"] = "INT16"
        p_setpoint_payload["access"] = "RW"
        path = _write_temp_yaml(payload)
        try:
            config = load_config(path)
        finally:
            os.unlink(path)

        lib_remote = config["PLANTS"]["lib"]["modbus"]["remote"]
        self.assertEqual(lib_remote["byte_order"], "big")
        self.assertEqual(lib_remote["word_order"], "msw_first")

        p_setpoint = lib_remote["points"]["p_setpoint"]
        self.assertEqual(p_setpoint["address"], 86)
        self.assertEqual(p_setpoint["format"], "int16")
        self.assertEqual(p_setpoint["access"], "rw")
        self.assertEqual(p_setpoint["word_count"], 1)
        self.assertEqual(p_setpoint["byte_count"], 2)
        self.assertEqual(p_setpoint["eng_per_count"], 0.1)

        self.assertIn("stop_command", config["PLANTS"]["lib"]["modbus"]["remote"]["points"])
        self.assertIn("start_command", config["PLANTS"]["vrfb"]["modbus"]["remote"]["points"])

    def test_rejects_missing_endpoint_byte_order(self):
        payload = _load_yaml("config.yaml")
        payload["plants"]["lib"]["modbus"]["local"].pop("byte_order", None)
        path = _write_temp_yaml(payload)
        try:
            with self.assertRaisesRegex(ValueError, "byte_order"):
                load_config(path)
        finally:
            os.unlink(path)

    def test_rejects_legacy_registers_schema(self):
        payload = _load_yaml("config.yaml")
        endpoint = payload["plants"]["lib"]["modbus"]["local"]
        points = endpoint.pop("points")
        endpoint.pop("byte_order", None)
        endpoint.pop("word_order", None)
        endpoint["registers"] = {name: spec["address"] for name, spec in points.items()}
        path = _write_temp_yaml(payload)
        try:
            with self.assertRaisesRegex(ValueError, "registers"):
                load_config(path)
        finally:
            os.unlink(path)

    def test_rejects_mixed_aggregate_and_per_phase_setpoint_points(self):
        payload = _load_yaml("config.yaml")
        payload["plants"]["lib"]["modbus"]["local"]["points"]["p_u_setpoint"] = {
            "address": 400,
            "format": "int16",
            "access": "rw",
            "unit": "kW",
            "eng_per_count": 0.1,
        }
        payload["plants"]["lib"]["modbus"]["local"]["points"]["p_v_setpoint"] = {
            "address": 401,
            "format": "int16",
            "access": "rw",
            "unit": "kW",
            "eng_per_count": 0.1,
        }
        payload["plants"]["lib"]["modbus"]["local"]["points"]["p_w_setpoint"] = {
            "address": 402,
            "format": "int16",
            "access": "rw",
            "unit": "kW",
            "eng_per_count": 0.1,
        }
        payload["plants"]["lib"]["modbus"]["local"]["points"]["q_u_setpoint"] = {
            "address": 403,
            "format": "int16",
            "access": "rw",
            "unit": "kvar",
            "eng_per_count": 0.1,
        }
        payload["plants"]["lib"]["modbus"]["local"]["points"]["q_v_setpoint"] = {
            "address": 404,
            "format": "int16",
            "access": "rw",
            "unit": "kvar",
            "eng_per_count": 0.1,
        }
        payload["plants"]["lib"]["modbus"]["local"]["points"]["q_w_setpoint"] = {
            "address": 405,
            "format": "int16",
            "access": "rw",
            "unit": "kvar",
            "eng_per_count": 0.1,
        }
        path = _write_temp_yaml(payload)
        try:
            with self.assertRaisesRegex(ValueError, "mutually exclusive"):
                load_config(path)
        finally:
            os.unlink(path)

    def test_rejects_partial_per_phase_setpoint_definition(self):
        payload = _load_yaml("config.yaml")
        points = payload["plants"]["lib"]["modbus"]["local"]["points"]
        points.pop("p_setpoint", None)
        points.pop("q_setpoint", None)
        points["p_u_setpoint"] = {"address": 86, "format": "int16", "access": "rw", "unit": "kW", "eng_per_count": 0.1}
        points["p_v_setpoint"] = {"address": 87, "format": "int16", "access": "rw", "unit": "kW", "eng_per_count": 0.1}
        points["q_u_setpoint"] = {"address": 89, "format": "int16", "access": "rw", "unit": "kvar", "eng_per_count": 0.1}
        path = _write_temp_yaml(payload)
        try:
            with self.assertRaisesRegex(ValueError, "per-phase Modbus setpoint points"):
                load_config(path)
        finally:
            os.unlink(path)

    def test_normalizes_unit_tokens_case_insensitively(self):
        payload = _load_yaml("config.yaml")
        endpoint = payload["plants"]["lib"]["modbus"]["local"]["points"]
        endpoint["p_setpoint"]["unit"] = "MW"
        endpoint["q_setpoint"]["unit"] = "Mvar"
        endpoint["v_poi"]["unit"] = "kV"
        endpoint["soc"]["unit"] = "%"
        path = _write_temp_yaml(payload)
        try:
            config = load_config(path)
        finally:
            os.unlink(path)

        points = config["PLANTS"]["lib"]["modbus"]["local"]["points"]
        self.assertEqual(points["p_setpoint"]["unit"], "mw")
        self.assertEqual(points["q_setpoint"]["unit"], "mvar")
        self.assertEqual(points["v_poi"]["unit"], "kv")
        self.assertEqual(points["soc"]["unit"], "pc")

    def test_accepts_grid_map_voltage_write_modbus_local_endpoint(self):
        payload = _load_yaml("config.yaml")
        payload.setdefault("grid_map", {}).setdefault("voltage_write_modbus", {})["local"] = {
            "host": "127.0.0.1",
            "port": 15020,
            "byte_order": "big",
            "word_order": "msw_first",
            "points": {
                "v_poi_write": {
                    "address": 400,
                    "format": "uint16",
                    "access": "w",
                    "unit": "V",
                    "eng_per_count": 1.0,
                }
            },
        }
        path = _write_temp_yaml(payload)
        try:
            config = load_config(path)
        finally:
            os.unlink(path)

        point = config["GRID_MAP_VOLTAGE_WRITE_MODBUS"]["local"]["points"]["v_poi_write"]
        self.assertEqual(point["address"], 400)
        self.assertEqual(point["access"], "w")
        self.assertEqual(point["unit"], "v")
        self.assertEqual(point["word_count"], 1)
        remote_point = config["GRID_MAP_VOLTAGE_WRITE_MODBUS"]["remote"]["points"]["v_poi_write"]
        self.assertEqual(remote_point["address"], 4)

    def test_accepts_missing_grid_map_voltage_write_modbus_remote_endpoint(self):
        payload = _load_yaml("config.yaml")
        payload.setdefault("grid_map", {}).setdefault("voltage_write_modbus", {}).pop("remote", None)
        path = _write_temp_yaml(payload)
        try:
            config = load_config(path)
        finally:
            os.unlink(path)

        self.assertIn("local", config["GRID_MAP_VOLTAGE_WRITE_MODBUS"])
        self.assertIn("remote", config["GRID_MAP_VOLTAGE_WRITE_MODBUS"])
        self.assertIsNone(config["GRID_MAP_VOLTAGE_WRITE_MODBUS"]["remote"])

    def test_rejects_plant_level_v_poi_write_point(self):
        payload = _load_yaml("config.yaml")
        payload["plants"]["lib"]["modbus"]["local"]["points"]["v_poi_write"] = {
            "address": 400,
            "format": "uint16",
            "access": "w",
            "unit": "V",
            "eng_per_count": 1.0,
        }
        path = _write_temp_yaml(payload)
        try:
            with self.assertRaisesRegex(ValueError, "grid_map.voltage_write_modbus.local.points.v_poi_write"):
                load_config(path)
        finally:
            os.unlink(path)

    def test_rejects_grid_map_voltage_write_modbus_without_v_poi_write(self):
        payload = _load_yaml("config.yaml")
        payload.setdefault("grid_map", {}).setdefault("voltage_write_modbus", {})["local"] = {
            "host": "127.0.0.1",
            "port": 15020,
            "byte_order": "big",
            "word_order": "msw_first",
            "points": {
                "trigger": {
                    "address": 401,
                    "format": "uint16",
                    "access": "w",
                    "unit": "raw",
                    "eng_per_count": 1.0,
                }
            },
        }
        path = _write_temp_yaml(payload)
        try:
            with self.assertRaisesRegex(ValueError, "Missing required Modbus points.*v_poi_write"):
                load_config(path)
        finally:
            os.unlink(path)

    def test_rejects_grid_map_voltage_write_modbus_with_extra_points(self):
        payload = _load_yaml("config.yaml")
        payload.setdefault("grid_map", {}).setdefault("voltage_write_modbus", {})["local"] = {
            "host": "127.0.0.1",
            "port": 15020,
            "byte_order": "big",
            "word_order": "msw_first",
            "points": {
                "v_poi_write": {
                    "address": 400,
                    "format": "uint16",
                    "access": "w",
                    "unit": "V",
                    "eng_per_count": 1.0,
                },
                "trigger": {
                    "address": 401,
                    "format": "uint16",
                    "access": "w",
                    "unit": "raw",
                    "eng_per_count": 1.0,
                },
            },
        }
        path = _write_temp_yaml(payload)
        try:
            with self.assertRaisesRegex(ValueError, "only v_poi_write is supported"):
                load_config(path)
        finally:
            os.unlink(path)

    def test_accepts_optional_trigger_point(self):
        payload = _load_yaml("config.yaml")
        payload["plants"]["lib"]["modbus"]["local"]["points"]["trigger"] = {
            "address": 401,
            "format": "uint16",
            "access": "w",
            "unit": "raw",
            "eng_per_count": 1.0,
        }
        path = _write_temp_yaml(payload)
        try:
            config = load_config(path)
        finally:
            os.unlink(path)

        point = config["PLANTS"]["lib"]["modbus"]["local"]["points"]["trigger"]
        self.assertEqual(point["address"], 401)
        self.assertEqual(point["access"], "w")
        self.assertEqual(point["unit"], "raw")
        self.assertEqual(point["word_count"], 1)

    def test_accepts_optional_q_control_mode_when_v_setpoint_is_present(self):
        payload = _load_yaml("config.yaml")
        payload["plants"]["lib"]["modbus"]["local"]["points"]["q_control_mode"] = {
            "address": 402,
            "format": "uint16",
            "access": "rw",
            "unit": "raw",
            "eng_per_count": 1.0,
        }
        payload["plants"]["lib"]["modbus"]["local"]["points"]["v_setpoint"] = {
            "address": 403,
            "format": "uint16",
            "access": "rw",
            "unit": "V",
            "eng_per_count": 1.0,
        }
        path = _write_temp_yaml(payload)
        try:
            config = load_config(path)
        finally:
            os.unlink(path)

        point = config["PLANTS"]["lib"]["modbus"]["local"]["points"]["q_control_mode"]
        v_point = config["PLANTS"]["lib"]["modbus"]["local"]["points"]["v_setpoint"]
        self.assertEqual(point["address"], 402)
        self.assertEqual(point["unit"], "raw")
        self.assertEqual(point["access"], "rw")
        self.assertEqual(v_point["address"], 403)
        self.assertEqual(v_point["unit"], "v")

    def test_accepts_non_loopback_local_host(self):
        payload = _load_yaml("config_test.yaml")
        payload["plants"]["lib"]["modbus"]["local"]["host"] = "10.117.133.21"
        path = _write_temp_yaml(payload)
        try:
            config = load_config(path)
        finally:
            os.unlink(path)

        self.assertEqual(config["PLANTS"]["lib"]["modbus"]["local"]["host"], "10.117.133.21")

    def test_rejects_duplicate_local_emulator_bind_address(self):
        payload = _load_yaml("config_test.yaml")
        payload["plants"]["vrfb"]["modbus"]["local"]["host"] = "127.0.0.1"
        payload["plants"]["vrfb"]["modbus"]["local"]["port"] = payload["plants"]["lib"]["modbus"]["local"]["port"]
        payload["plants"]["lib"]["modbus"]["local"]["host"] = "127.0.0.1"
        path = _write_temp_yaml(payload)
        try:
            with self.assertRaisesRegex(ValueError, "distinct host/port pairs"):
                load_config(path)
        finally:
            os.unlink(path)

    def test_accepts_duplicate_non_loopback_local_endpoints(self):
        payload = _load_yaml("config_test.yaml")
        payload["plants"]["lib"]["modbus"]["local"]["host"] = "10.117.133.21"
        payload["plants"]["lib"]["modbus"]["local"]["port"] = 502
        payload["plants"]["vrfb"]["modbus"]["local"]["host"] = "10.117.133.21"
        payload["plants"]["vrfb"]["modbus"]["local"]["port"] = 502
        path = _write_temp_yaml(payload)
        try:
            config = load_config(path)
        finally:
            os.unlink(path)

        self.assertEqual(config["PLANTS"]["lib"]["modbus"]["local"]["host"], "10.117.133.21")
        self.assertEqual(config["PLANTS"]["vrfb"]["modbus"]["local"]["host"], "10.117.133.21")
        self.assertEqual(config["PLANTS"]["lib"]["modbus"]["local"]["port"], 502)
        self.assertEqual(config["PLANTS"]["vrfb"]["modbus"]["local"]["port"], 502)

    def test_rejects_q_control_mode_without_v_setpoint(self):
        payload = _load_yaml("config.yaml")
        payload["plants"]["lib"]["modbus"]["local"]["points"].pop("v_setpoint", None)
        payload["plants"]["lib"]["modbus"]["local"]["points"]["q_control_mode"] = {
            "address": 402,
            "format": "uint16",
            "access": "rw",
            "unit": "raw",
            "eng_per_count": 1.0,
        }
        path = _write_temp_yaml(payload)
        try:
            with self.assertRaisesRegex(ValueError, "v_setpoint"):
                load_config(path)
        finally:
            os.unlink(path)

    def test_rejects_inverted_power_limits(self):
        payload = _load_yaml("config.yaml")
        payload["plants"]["lib"]["model"]["power_limits"]["p_min_kw"] = 10.0
        payload["plants"]["lib"]["model"]["power_limits"]["p_max_kw"] = 5.0
        path = _write_temp_yaml(payload)
        try:
            with self.assertRaisesRegex(ValueError, "p_min_kw must be <= p_max_kw"):
                load_config(path)
        finally:
            os.unlink(path)

    def test_accepts_missing_soc_point_for_one_endpoint(self):
        payload = _load_yaml("config.yaml")
        payload["plants"]["lib"]["modbus"]["remote"]["points"].pop("soc", None)
        path = _write_temp_yaml(payload)
        try:
            config = load_config(path)
        finally:
            os.unlink(path)

        points = config["PLANTS"]["lib"]["modbus"]["remote"]["points"]
        self.assertNotIn("soc", points)
        self.assertNotIn("PLANT_REMOTE_SOC_REGISTER", config)

    def test_rejects_invalid_point_unit_for_quantity(self):
        payload = _load_yaml("config.yaml")
        payload["plants"]["lib"]["modbus"]["local"]["points"]["p_setpoint"]["unit"] = "kV"
        path = _write_temp_yaml(payload)
        try:
            with self.assertRaisesRegex(ValueError, "Invalid unit"):
                load_config(path)
        finally:
            os.unlink(path)

    def test_rejects_legacy_model_voltage_key(self):
        payload = _load_yaml("config.yaml")
        model = payload["plants"]["lib"]["model"]
        model["poi_voltage_v"] = 20000.0
        path = _write_temp_yaml(payload)
        try:
            with self.assertRaisesRegex(ValueError, "poi_voltage_v"):
                load_config(path)
        finally:
            os.unlink(path)

    def test_rejects_legacy_voltage_tolerance_key(self):
        payload = _load_yaml("config.yaml")
        tol = payload.setdefault("recording", {}).setdefault("compression", {}).setdefault("tolerances", {})
        tol["v_poi_pu"] = 0.001
        path = _write_temp_yaml(payload)
        try:
            with self.assertRaisesRegex(ValueError, "v_poi_pu"):
                load_config(path)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
