import os
import threading
import tempfile
import unittest
from contextlib import chdir
from unittest.mock import patch

import pandas as pd

from measurement.agent import measurement_agent
from measurement.storage import MEASUREMENT_COLUMNS
from modbus.codec import encode_point_internal_words


def _build_shared_data(lib_file_path, vrfb_file_path=None, *, transport_mode="local"):
    return {
        "lock": threading.Lock(),
        "shutdown_event": threading.Event(),
        "transport_mode": transport_mode,
        "api_password": None,
        "measurements_filename_by_plant": {"lib": lib_file_path, "vrfb": vrfb_file_path},
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
            "lib": {"name": "LIB", "model": {"capacity_kwh": 500.0, "poi_voltage_kv": 20.0}},
            "vrfb": {"name": "VRFB", "model": {"capacity_kwh": 400.0, "poi_voltage_kv": 0.22}},
        },
        "MEASUREMENT_PERIOD_S": 0.2,
        "MEASUREMENTS_WRITE_PERIOD_S": 0.2,
        "ISTENTORE_POST_MEASUREMENTS_IN_API_MODE": False,
        "ISTENTORE_MEASUREMENT_POST_PERIOD_S": 60,
        "ISTENTORE_MEASUREMENT_POST_QUEUE_MAXLEN": 10,
        "ISTENTORE_MEASUREMENT_POST_RETRY_INITIAL_S": 1,
        "ISTENTORE_MEASUREMENT_POST_RETRY_MAX_S": 2,
    }


def _aggregate_endpoint(host, port):
    return {
        "host": host,
        "port": port,
        "byte_order": "big",
        "word_order": "msw_first",
        "points": {
            "p_setpoint": {"name": "p_setpoint", "address": 1, "format": "int16", "word_count": 1, "unit": "kW", "eng_per_count": 0.1},
            "p_battery": {"name": "p_battery", "address": 2, "format": "int16", "word_count": 1, "unit": "kW", "eng_per_count": 0.1},
            "q_setpoint": {"name": "q_setpoint", "address": 3, "format": "int16", "word_count": 1, "unit": "kvar", "eng_per_count": 0.1},
            "q_battery": {"name": "q_battery", "address": 4, "format": "int16", "word_count": 1, "unit": "kvar", "eng_per_count": 0.1},
            "soc": {"name": "soc", "address": 5, "format": "uint16", "word_count": 1, "unit": "pu", "eng_per_count": 0.0001},
            "p_poi": {"name": "p_poi", "address": 6, "format": "int16", "word_count": 1, "unit": "kW", "eng_per_count": 0.1},
            "q_poi": {"name": "q_poi", "address": 7, "format": "int16", "word_count": 1, "unit": "kvar", "eng_per_count": 0.1},
            "v_poi": {"name": "v_poi", "address": 8, "format": "uint16", "word_count": 1, "unit": "kV", "eng_per_count": 0.1},
        },
    }


def _per_phase_endpoint(host, port):
    return {
        "host": host,
        "port": port,
        "byte_order": "big",
        "word_order": "msw_first",
        "points": {
            "p_u_setpoint": {"name": "p_u_setpoint", "address": 1, "format": "float32", "word_count": 2, "unit": "W", "eng_per_count": 1.0},
            "p_v_setpoint": {"name": "p_v_setpoint", "address": 3, "format": "float32", "word_count": 2, "unit": "W", "eng_per_count": 1.0},
            "p_w_setpoint": {"name": "p_w_setpoint", "address": 5, "format": "float32", "word_count": 2, "unit": "W", "eng_per_count": 1.0},
            "p_battery": {"name": "p_battery", "address": 7, "format": "float32", "word_count": 2, "unit": "W", "eng_per_count": 1.0},
            "q_u_setpoint": {"name": "q_u_setpoint", "address": 9, "format": "float32", "word_count": 2, "unit": "var", "eng_per_count": 1.0},
            "q_v_setpoint": {"name": "q_v_setpoint", "address": 11, "format": "float32", "word_count": 2, "unit": "var", "eng_per_count": 1.0},
            "q_w_setpoint": {"name": "q_w_setpoint", "address": 13, "format": "float32", "word_count": 2, "unit": "var", "eng_per_count": 1.0},
            "q_battery": {"name": "q_battery", "address": 15, "format": "float32", "word_count": 2, "unit": "var", "eng_per_count": 1.0},
            "p_poi": {"name": "p_poi", "address": 17, "format": "float32", "word_count": 2, "unit": "W", "eng_per_count": 1.0},
            "q_poi": {"name": "q_poi", "address": 19, "format": "float32", "word_count": 2, "unit": "var", "eng_per_count": 1.0},
            "v_poi": {"name": "v_poi", "address": 21, "format": "float32", "word_count": 2, "unit": "V", "eng_per_count": 1.0},
        },
    }


def _write_point_to_register_map(register_map, endpoint, point_name, internal_value):
    spec = endpoint["points"][point_name]
    words = encode_point_internal_words(endpoint, spec, internal_value)
    start_addr = int(spec["address"])
    for offset, word in enumerate(words):
        register_map[start_addr + offset] = int(word)


class _MappedSamplingModbusClient:
    register_map_by_endpoint = {}

    def __init__(self, host, port):
        self.endpoint_key = (host, port)
        self.is_open = False

    def open(self):
        self.is_open = True
        return True

    def close(self):
        self.is_open = False

    def read_holding_registers(self, address, count):
        register_map = self.register_map_by_endpoint.get(self.endpoint_key, {})
        regs = []
        for reg_addr in range(int(address), int(address) + int(count)):
            if reg_addr not in register_map:
                return None
            regs.append(int(register_map[reg_addr]) & 0xFFFF)
        return regs


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
                    "v_poi_kV": 1.0,
                }
                pd.DataFrame([initial_row], columns=MEASUREMENT_COLUMNS).to_csv(lib_file_path, index=False)
                initial_len = len(pd.read_csv(lib_file_path))

                shared_data = _build_shared_data(lib_file_path=lib_file_path)
                config = _build_config()

                # End the agent loop shortly after startup; this test targets record-start stability.
                stop_timer = threading.Timer(1.4, shared_data["shutdown_event"].set)
                stop_timer.start()
                try:
                    with patch(
                        "measurement.agent.sampling_get_transport_endpoint",
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
                        "measurement.agent.sampling_ensure_client",
                        return_value=None,
                    ), patch(
                        "measurement.agent.sampling_take_measurement",
                        return_value=None,
                    ):
                        measurement_agent(config, shared_data)
                finally:
                    stop_timer.cancel()

                final_df = pd.read_csv(lib_file_path)
                self.assertGreater(len(final_df), initial_len)

    def test_dual_plant_recording_keeps_lib_aggregate_and_records_vrfb_per_phase(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with chdir(tmpdir):
                os.makedirs("data", exist_ok=True)

                shared_data = _build_shared_data(
                    lib_file_path="data/requested_lib.csv",
                    vrfb_file_path="data/requested_vrfb.csv",
                    transport_mode="remote",
                )
                config = _build_config()

                lib_endpoint = _aggregate_endpoint("127.0.0.1", 15020)
                vrfb_endpoint = _per_phase_endpoint("127.0.0.1", 15021)

                lib_registers = {}
                for point_name, value in (
                    ("p_setpoint", 10.0),
                    ("p_battery", 9.0),
                    ("q_setpoint", 1.0),
                    ("q_battery", 0.8),
                    ("soc", 0.6),
                    ("p_poi", 8.5),
                    ("q_poi", 0.7),
                    ("v_poi", 20.0),
                ):
                    _write_point_to_register_map(lib_registers, lib_endpoint, point_name, value)

                vrfb_registers = {}
                for point_name, value in (
                    ("p_u_setpoint", 4.0),
                    ("p_v_setpoint", 5.5),
                    ("p_w_setpoint", 6.5),
                    ("p_battery", 0.0),
                    ("q_u_setpoint", 0.25),
                    ("q_v_setpoint", 0.5),
                    ("q_w_setpoint", 0.75),
                    ("q_battery", 0.0),
                    ("p_poi", 0.0),
                    ("q_poi", 0.0),
                    ("v_poi", 0.22),
                ):
                    _write_point_to_register_map(vrfb_registers, vrfb_endpoint, point_name, value)

                _MappedSamplingModbusClient.register_map_by_endpoint = {
                    (lib_endpoint["host"], lib_endpoint["port"]): lib_registers,
                    (vrfb_endpoint["host"], vrfb_endpoint["port"]): vrfb_registers,
                }

                stop_timer = threading.Timer(1.4, shared_data["shutdown_event"].set)
                stop_timer.start()
                try:
                    with patch(
                        "measurement.agent.sampling_get_transport_endpoint",
                        side_effect=lambda _config, plant_id, _transport_mode: {
                            "lib": lib_endpoint,
                            "vrfb": vrfb_endpoint,
                        }[plant_id],
                    ), patch(
                        "measurement.agent.resolve_startup_soc_seed",
                        return_value={"soc_pu": 0.55, "source": "test_seed", "file_path": None, "timestamp": None},
                    ), patch(
                        "measurement.sampling.ModbusClient",
                        _MappedSamplingModbusClient,
                    ):
                        measurement_agent(config, shared_data)
                finally:
                    stop_timer.cancel()

                lib_output_path = sorted(path for path in os.listdir("data") if path.endswith("_lib.csv"))[-1]
                vrfb_output_path = sorted(path for path in os.listdir("data") if path.endswith("_vrfb.csv"))[-1]

                lib_rows = pd.read_csv(os.path.join("data", lib_output_path)).dropna(subset=["battery_active_power_kw"]).reset_index(drop=True)
                vrfb_rows = pd.read_csv(os.path.join("data", vrfb_output_path)).dropna(subset=["battery_active_power_kw"]).reset_index(drop=True)

                self.assertFalse(lib_rows.empty)
                self.assertFalse(vrfb_rows.empty)
                self.assertAlmostEqual(float(lib_rows.iloc[-1]["p_setpoint_kw"]), 10.0, places=6)
                self.assertAlmostEqual(float(lib_rows.iloc[-1]["q_setpoint_kvar"]), 1.0, places=6)
                self.assertAlmostEqual(float(lib_rows.iloc[-1]["v_setpoint_pu"]), 1.0, places=6)
                self.assertAlmostEqual(float(vrfb_rows.iloc[-1]["p_setpoint_kw"]), 16.0, places=6)
                self.assertAlmostEqual(float(vrfb_rows.iloc[-1]["q_setpoint_kvar"]), 1.5, places=6)
                self.assertAlmostEqual(float(vrfb_rows.iloc[-1]["v_setpoint_pu"]), 1.0, places=6)
                self.assertAlmostEqual(float(vrfb_rows.iloc[-1]["soc_pu"]), 0.55, places=6)


if __name__ == "__main__":
    unittest.main()
