import os
import threading
import unittest
from contextlib import chdir
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from measurement.agent import measurement_agent
from measurement.sampling import take_measurement
from modbus.codec import encode_point_internal_words


def _build_shared_data(recording_path):
    return {
        "lock": threading.Lock(),
        "shutdown_event": threading.Event(),
        "transport_mode": "local",
        "api_password": None,
        "measurements_filename_by_plant": {"lib": recording_path},
        "current_file_path_by_plant": {"lib": None},
        "current_file_df_by_plant": {"lib": pd.DataFrame()},
        "pending_rows_by_file": {},
        "measurements_df": pd.DataFrame(),
        "measurement_post_status": {},
    }


def _build_config(*, capacity_kwh=1000.0, measurement_period_s=0.2):
    return {
        "TIMEZONE_NAME": "Europe/Madrid",
        "PLANT_IDS": ("lib",),
        "PLANTS": {
            "lib": {
                "name": "LIB",
                "model": {"capacity_kwh": capacity_kwh, "poi_voltage_kv": 20.0},
                "measurement_series": {"soc": 4, "p": 6, "q": 7, "v": 8},
            }
        },
        "MEASUREMENT_PERIOD_S": measurement_period_s,
        "MEASUREMENTS_WRITE_PERIOD_S": 0.15,
        "ISTENTORE_POST_MEASUREMENTS_IN_API_MODE": False,
        "ISTENTORE_MEASUREMENT_POST_PERIOD_S": 60,
        "ISTENTORE_MEASUREMENT_POST_QUEUE_MAXLEN": 10,
        "ISTENTORE_MEASUREMENT_POST_RETRY_INITIAL_S": 1,
        "ISTENTORE_MEASUREMENT_POST_RETRY_MAX_S": 2,
    }


def _fake_endpoint(*_args, **_kwargs):
    return {"host": "localhost", "port": 5020, "points": {}}


def _measurement_runner(shared_data, samples):
    state = {"idx": 0}

    def _run(_client, _endpoint, measurement_timestamp, _tz, _plant_id):
        index = state["idx"]
        if index >= len(samples):
            return None
        sample = dict(samples[index] or {})
        row = dict(sample.get("row", {}) or {})
        row["timestamp"] = measurement_timestamp
        state["idx"] += 1
        if state["idx"] >= len(samples):
            shared_data["shutdown_event"].set()
        return {"row": row, "has_real_soc": bool(sample.get("has_real_soc", False))}

    return _run


def _load_real_rows(config, shared_data, samples, startup_seed):
    with TemporaryDirectory() as tmpdir:
        with chdir(tmpdir):
            os.makedirs("data", exist_ok=True)
            with patch("measurement.agent.sampling_get_transport_endpoint", side_effect=_fake_endpoint), patch(
                "measurement.agent.sampling_ensure_client",
                return_value=object(),
            ), patch(
                "measurement.agent.sampling_take_measurement",
                side_effect=_measurement_runner(shared_data, samples),
            ), patch(
                "measurement.agent.resolve_startup_soc_seed",
                return_value=startup_seed,
            ):
                measurement_agent(config, shared_data)

            output_paths = sorted(path for path in os.listdir("data") if path.endswith("_lib.csv"))
            output_df = pd.read_csv(os.path.join("data", output_paths[-1]))
            return output_df.dropna(subset=["battery_active_power_kw"]).reset_index(drop=True)


def _sampling_endpoint(include_soc=True):
    points = {
        "p_setpoint": {"name": "p_setpoint", "address": 1, "format": "int16", "word_count": 1, "unit": "kW", "eng_per_count": 0.1},
        "p_battery": {"name": "p_battery", "address": 2, "format": "int16", "word_count": 1, "unit": "kW", "eng_per_count": 0.1},
        "q_setpoint": {"name": "q_setpoint", "address": 3, "format": "int16", "word_count": 1, "unit": "kvar", "eng_per_count": 0.1},
        "q_battery": {"name": "q_battery", "address": 4, "format": "int16", "word_count": 1, "unit": "kvar", "eng_per_count": 0.1},
        "p_poi": {"name": "p_poi", "address": 5, "format": "int16", "word_count": 1, "unit": "kW", "eng_per_count": 0.1},
        "q_poi": {"name": "q_poi", "address": 6, "format": "int16", "word_count": 1, "unit": "kvar", "eng_per_count": 0.1},
        "v_poi": {"name": "v_poi", "address": 7, "format": "uint16", "word_count": 1, "unit": "kV", "eng_per_count": 0.1},
    }
    if include_soc:
        points["soc"] = {"name": "soc", "address": 20, "format": "uint16", "word_count": 1, "unit": "pu", "eng_per_count": 0.0001}
    return {"byte_order": "big", "word_order": "msw_first", "points": points}


class _SamplingClient:
    def __init__(self, register_map):
        self.register_map = dict(register_map)
        self.is_open = True

    def open(self):
        self.is_open = True
        return True

    def read_holding_registers(self, address, count):
        regs = []
        for reg_addr in range(int(address), int(address) + int(count)):
            if reg_addr not in self.register_map:
                return None
            regs.append(int(self.register_map[reg_addr]) & 0xFFFF)
        return regs


class MeasurementSocFallbackTests(unittest.TestCase):
    def test_missing_soc_uses_startup_seed_then_integrates_battery_power(self):
        config = _build_config(capacity_kwh=1000.0, measurement_period_s=0.2)
        shared_data = _build_shared_data("data/20990101_lib.csv")
        rows = _load_real_rows(
            config,
            shared_data,
            [
                {
                    "has_real_soc": False,
                    "row": {
                        "p_setpoint_kw": 0.0,
                        "battery_active_power_kw": 0.0,
                        "q_setpoint_kvar": 0.0,
                        "battery_reactive_power_kvar": 0.0,
                        "soc_pu": None,
                        "p_poi_kw": 0.0,
                        "q_poi_kvar": 0.0,
                        "v_poi_kV": 20.0,
                    },
                },
                {
                    "has_real_soc": False,
                    "row": {
                        "p_setpoint_kw": 1800000.0,
                        "battery_active_power_kw": 1800000.0,
                        "q_setpoint_kvar": 0.0,
                        "battery_reactive_power_kvar": 0.0,
                        "soc_pu": None,
                        "p_poi_kw": 1800000.0,
                        "q_poi_kvar": 0.0,
                        "v_poi_kV": 20.0,
                    },
                },
            ],
            startup_seed={"soc_pu": 0.63, "source": "startup_fallback", "file_path": None, "timestamp": None},
        )

        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(float(rows.iloc[0]["soc_pu"]), 0.63, places=6)
        self.assertAlmostEqual(float(rows.iloc[1]["soc_pu"]), 0.53, places=3)

    def test_real_soc_sync_is_used_before_missing_soc_fallback(self):
        config = _build_config(capacity_kwh=1000.0, measurement_period_s=0.2)
        shared_data = _build_shared_data("data/20990101_lib.csv")
        rows = _load_real_rows(
            config,
            shared_data,
            [
                {
                    "has_real_soc": True,
                    "row": {
                        "p_setpoint_kw": 0.0,
                        "battery_active_power_kw": 0.0,
                        "q_setpoint_kvar": 0.0,
                        "battery_reactive_power_kvar": 0.0,
                        "soc_pu": 0.8,
                        "p_poi_kw": 0.0,
                        "q_poi_kvar": 0.0,
                        "v_poi_kV": 20.0,
                    },
                },
                {
                    "has_real_soc": False,
                    "row": {
                        "p_setpoint_kw": 1800000.0,
                        "battery_active_power_kw": 1800000.0,
                        "q_setpoint_kvar": 0.0,
                        "battery_reactive_power_kvar": 0.0,
                        "soc_pu": None,
                        "p_poi_kw": 1800000.0,
                        "q_poi_kvar": 0.0,
                        "v_poi_kV": 20.0,
                    },
                },
            ],
            startup_seed={"soc_pu": 0.2, "source": "startup_fallback", "file_path": None, "timestamp": None},
        )

        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(float(rows.iloc[0]["soc_pu"]), 0.8, places=6)
        self.assertAlmostEqual(float(rows.iloc[1]["soc_pu"]), 0.7, places=3)

    def test_sampling_allows_missing_soc_point_but_not_unreadable_configured_soc(self):
        endpoint_without_soc = _sampling_endpoint(include_soc=False)
        endpoint_with_soc = _sampling_endpoint(include_soc=True)
        register_map = {}
        for point_name, value in (
            ("p_setpoint", 10.0),
            ("p_battery", 9.0),
            ("q_setpoint", 1.0),
            ("q_battery", 1.0),
            ("p_poi", 8.0),
            ("q_poi", 1.0),
            ("v_poi", 20.0),
        ):
            spec = endpoint_with_soc["points"][point_name]
            words = encode_point_internal_words(endpoint_with_soc, spec, value)
            register_map[int(spec["address"])] = int(words[0])

        client = _SamplingClient(register_map)
        tz = ZoneInfo("Europe/Madrid")
        missing_soc_result = take_measurement(client, endpoint_without_soc, pd.Timestamp("2026-04-13T10:00:00+02:00"), tz, "lib")
        unreadable_soc_result = take_measurement(client, endpoint_with_soc, pd.Timestamp("2026-04-13T10:00:00+02:00"), tz, "lib")

        self.assertIsNotNone(missing_soc_result)
        self.assertFalse(bool(missing_soc_result["has_real_soc"]))
        self.assertIsNone(missing_soc_result["row"]["soc_pu"])
        self.assertIsNone(unreadable_soc_result)


if __name__ == "__main__":
    unittest.main()
