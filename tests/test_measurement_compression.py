import glob
import os
import threading
import unittest
from contextlib import chdir
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from measurement_agent import measurement_agent
from measurement_storage import MEASUREMENT_VALUE_COLUMNS, append_rows_to_csv as storage_append_rows_to_csv


def _build_shared_data(recording_path):
    return {
        "lock": threading.Lock(),
        "shutdown_event": threading.Event(),
        "transport_mode": "local",
        "active_schedule_source": "manual",
        "api_password": None,
        "measurements_filename_by_plant": {"lib": recording_path},
        "current_file_path_by_plant": {"lib": None},
        "current_file_df_by_plant": {"lib": pd.DataFrame()},
        "pending_rows_by_file": {},
        "measurements_df": pd.DataFrame(),
        "measurement_post_status": {},
    }


def _build_config(
    compression_enabled=True,
    measurement_period_s=0.1,
    write_period_s=0.15,
    compression_max_kept_gap_s=3600.0,
):
    return {
        "TIMEZONE_NAME": "Europe/Madrid",
        "PLANT_IDS": ("lib",),
        "PLANTS": {
            "lib": {
                "name": "LIB",
                "measurement_series": {"soc": 4, "p": 6, "q": 7, "v": 8},
                "model": {"capacity_kwh": 500.0, "poi_voltage_v": 20000.0},
            }
        },
        "MEASUREMENT_PERIOD_S": measurement_period_s,
        "MEASUREMENTS_WRITE_PERIOD_S": write_period_s,
        "MEASUREMENT_COMPRESSION_ENABLED": compression_enabled,
        "MEASUREMENT_COMPRESSION_MAX_KEPT_GAP_S": compression_max_kept_gap_s,
        "MEASUREMENT_COMPRESSION_TOLERANCES": {
            "p_setpoint_kw": 0.0,
            "battery_active_power_kw": 0.1,
            "q_setpoint_kvar": 0.0,
            "battery_reactive_power_kvar": 0.1,
            "soc_pu": 0.001,
            "p_poi_kw": 0.1,
            "q_poi_kvar": 0.1,
            "v_poi_pu": 0.001,
        },
        "ISTENTORE_POST_MEASUREMENTS_IN_API_MODE": False,
        "ISTENTORE_MEASUREMENT_POST_PERIOD_S": 60,
        "ISTENTORE_MEASUREMENT_POST_QUEUE_MAXLEN": 10,
        "ISTENTORE_MEASUREMENT_POST_RETRY_INITIAL_S": 1,
        "ISTENTORE_MEASUREMENT_POST_RETRY_MAX_S": 2,
    }


def _fake_endpoint(*_args, **_kwargs):
    return {
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
    }


def _make_row_sequence_runner(shared_data, rows):
    state = {"idx": 0}

    def _run(_client, _endpoint, measurement_timestamp, _tz, _plant_id):
        index = state["idx"]
        if index >= len(rows):
            return None

        row = dict(rows[index])
        row["timestamp"] = measurement_timestamp
        state["idx"] += 1

        if state["idx"] >= len(rows):
            shared_data["shutdown_event"].set()
        return row

    return _run


def _run_agent_and_load_output(config, shared_data, sample_rows, append_wrapper=None):
    patchers = [
        patch("measurement_agent.sampling_get_transport_endpoint", side_effect=_fake_endpoint),
        patch("measurement_agent.sampling_ensure_client", return_value=object()),
        patch(
            "measurement_agent.sampling_take_measurement",
            side_effect=_make_row_sequence_runner(shared_data, sample_rows),
        ),
    ]
    if append_wrapper is not None:
        patchers.append(patch("measurement_agent.append_rows_to_csv", side_effect=append_wrapper))

    with patchers[0], patchers[1], patchers[2]:
        if append_wrapper is not None:
            with patchers[3]:
                measurement_agent(config, shared_data)
        else:
            measurement_agent(config, shared_data)

    paths = sorted(glob.glob("data/*_lib.csv"))
    if not paths:
        raise AssertionError("Expected at least one LIB measurement file.")
    return pd.read_csv(paths[-1])


class MeasurementCompressionTests(unittest.TestCase):
    def test_compression_enabled_compacts_stable_run(self):
        samples = [
            {
                "p_setpoint_kw": 100.0,
                "battery_active_power_kw": 100.0,
                "q_setpoint_kvar": 0.0,
                "battery_reactive_power_kvar": 0.0,
                "soc_pu": 0.50000,
                "p_poi_kw": 100.0,
                "q_poi_kvar": 0.0,
                "v_poi_pu": 1.0,
            },
            {
                "p_setpoint_kw": 100.0,
                "battery_active_power_kw": 100.0,
                "q_setpoint_kvar": 0.0,
                "battery_reactive_power_kvar": 0.0,
                "soc_pu": 0.50005,
                "p_poi_kw": 100.0,
                "q_poi_kvar": 0.0,
                "v_poi_pu": 1.0,
            },
            {
                "p_setpoint_kw": 100.0,
                "battery_active_power_kw": 100.0,
                "q_setpoint_kvar": 0.0,
                "battery_reactive_power_kvar": 0.0,
                "soc_pu": 0.50008,
                "p_poi_kw": 100.0,
                "q_poi_kvar": 0.0,
                "v_poi_pu": 1.0,
            },
            {
                "p_setpoint_kw": 100.0,
                "battery_active_power_kw": 100.0,
                "q_setpoint_kvar": 0.0,
                "battery_reactive_power_kvar": 0.0,
                "soc_pu": 0.50009,
                "p_poi_kw": 100.0,
                "q_poi_kvar": 0.0,
                "v_poi_pu": 1.0,
            },
        ]

        with TemporaryDirectory() as tmpdir:
            with chdir(tmpdir):
                os.makedirs("data", exist_ok=True)
                shared_data = _build_shared_data("data/20990101_lib.csv")
                config = _build_config(compression_enabled=True, measurement_period_s=0.1, write_period_s=0.15)
                output_df = _run_agent_and_load_output(config, shared_data, samples)

        real_rows = output_df.dropna(subset=["battery_active_power_kw"])
        self.assertEqual(len(real_rows), 2)
        self.assertAlmostEqual(float(real_rows.iloc[-1]["soc_pu"]), samples[-1]["soc_pu"], places=6)
        self.assertTrue(all(pd.isna(output_df.iloc[0][column]) for column in MEASUREMENT_VALUE_COLUMNS))
        self.assertTrue(all(pd.isna(output_df.iloc[-1][column]) for column in MEASUREMENT_VALUE_COLUMNS))

    def test_compression_disabled_keeps_all_real_rows(self):
        samples = []
        for idx in range(6):
            samples.append(
                {
                    "p_setpoint_kw": 120.0,
                    "battery_active_power_kw": 120.0,
                    "q_setpoint_kvar": 0.0,
                    "battery_reactive_power_kvar": 0.0,
                    "soc_pu": 0.50 + (idx * 0.00005),
                    "p_poi_kw": 120.0,
                    "q_poi_kvar": 0.0,
                    "v_poi_pu": 1.0,
                }
            )

        with TemporaryDirectory() as tmpdir:
            with chdir(tmpdir):
                os.makedirs("data", exist_ok=True)
                shared_data = _build_shared_data("data/20990101_lib.csv")
                config = _build_config(compression_enabled=False, measurement_period_s=0.1, write_period_s=0.15)
                output_df = _run_agent_and_load_output(config, shared_data, samples)

        real_rows = output_df.dropna(subset=["battery_active_power_kw"])
        self.assertEqual(len(real_rows), len(samples))
        self.assertTrue(all(pd.isna(output_df.iloc[0][column]) for column in MEASUREMENT_VALUE_COLUMNS))
        self.assertTrue(all(pd.isna(output_df.iloc[-1][column]) for column in MEASUREMENT_VALUE_COLUMNS))

    def test_compression_retains_latest_point_across_periodic_flushes(self):
        samples = []
        for idx in range(12):
            samples.append(
                {
                    "p_setpoint_kw": 80.0,
                    "battery_active_power_kw": 80.0,
                    "q_setpoint_kvar": 0.0,
                    "battery_reactive_power_kvar": 0.0,
                    "soc_pu": 0.48 + (idx * 0.00005),
                    "p_poi_kw": 80.0,
                    "q_poi_kvar": 0.0,
                    "v_poi_pu": 1.0,
                }
            )

        append_calls = {"count": 0}

        def counting_append(file_path, rows, tz):
            append_calls["count"] += 1
            return storage_append_rows_to_csv(file_path, rows, tz)

        with TemporaryDirectory() as tmpdir:
            with chdir(tmpdir):
                os.makedirs("data", exist_ok=True)
                shared_data = _build_shared_data("data/20990101_lib.csv")
                config = _build_config(compression_enabled=True, measurement_period_s=0.1, write_period_s=0.1)
                output_df = _run_agent_and_load_output(
                    config,
                    shared_data,
                    samples,
                    append_wrapper=counting_append,
                )

        real_rows = output_df.dropna(subset=["battery_active_power_kw"])
        self.assertGreaterEqual(append_calls["count"], 3)
        self.assertEqual(len(real_rows), 2)
        self.assertAlmostEqual(float(real_rows.iloc[-1]["soc_pu"]), samples[-1]["soc_pu"], places=6)

    def test_compression_keeps_similar_row_when_gap_exceeds_configured_interval(self):
        samples = []
        for idx in range(6):
            samples.append(
                {
                    "p_setpoint_kw": 60.0,
                    "battery_active_power_kw": 60.0,
                    "q_setpoint_kvar": 0.0,
                    "battery_reactive_power_kvar": 0.0,
                    "soc_pu": 0.40 + (idx * 0.00005),
                    "p_poi_kw": 60.0,
                    "q_poi_kvar": 0.0,
                    "v_poi_pu": 1.0,
                }
            )

        with TemporaryDirectory() as tmpdir:
            with chdir(tmpdir):
                os.makedirs("data", exist_ok=True)
                shared_data = _build_shared_data("data/20990101_lib.csv")
                config = _build_config(
                    compression_enabled=True,
                    measurement_period_s=0.1,
                    write_period_s=0.15,
                    compression_max_kept_gap_s=0.25,
                )
                output_df = _run_agent_and_load_output(config, shared_data, samples)

        real_rows = output_df.dropna(subset=["battery_active_power_kw"])
        self.assertEqual(len(real_rows), 4)
        self.assertAlmostEqual(float(real_rows.iloc[-1]["soc_pu"]), samples[-1]["soc_pu"], places=6)

    def test_compression_tolerance_uses_last_kept_row_to_prevent_drift(self):
        samples = []
        for idx in range(8):
            samples.append(
                {
                    "p_setpoint_kw": 75.0,
                    "battery_active_power_kw": 75.0,
                    "q_setpoint_kvar": 0.0,
                    "battery_reactive_power_kvar": 0.0,
                    "soc_pu": 0.50 + (idx * 0.0004),
                    "p_poi_kw": 75.0,
                    "q_poi_kvar": 0.0,
                    "v_poi_pu": 1.0,
                }
            )

        with TemporaryDirectory() as tmpdir:
            with chdir(tmpdir):
                os.makedirs("data", exist_ok=True)
                shared_data = _build_shared_data("data/20990101_lib.csv")
                config = _build_config(
                    compression_enabled=True,
                    measurement_period_s=0.1,
                    write_period_s=0.15,
                    compression_max_kept_gap_s=3600.0,
                )
                output_df = _run_agent_and_load_output(config, shared_data, samples)

        real_rows = output_df.dropna(subset=["battery_active_power_kw"])
        self.assertEqual(len(real_rows), 4)
        self.assertAlmostEqual(float(real_rows.iloc[-1]["soc_pu"]), samples[-1]["soc_pu"], places=6)


if __name__ == "__main__":
    unittest.main()
