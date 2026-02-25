import os
import glob
import threading
import time
import unittest
from contextlib import chdir
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from config_loader import load_config
import manual_schedule_manager as msm
from measurement_storage import MEASUREMENT_VALUE_COLUMNS
from measurement_agent import measurement_agent
from plant_agent import plant_agent
from scheduler_agent import scheduler_agent
from time_utils import now_tz


class _FakeDataBank:
    def __init__(self):
        self._registers = {}
        self._lock = threading.Lock()

    def set_holding_registers(self, address, values):
        addr = int(address)
        with self._lock:
            for offset, value in enumerate(values):
                self._registers[addr + offset] = int(value)

    def get_holding_registers(self, address, count):
        addr = int(address)
        cnt = int(count)
        with self._lock:
            return [self._registers.get(addr + offset, 0) for offset in range(cnt)]


class _FakeModbusRegistry:
    _lock = threading.Lock()
    _servers = {}

    @classmethod
    def clear(cls):
        with cls._lock:
            cls._servers = {}

    @classmethod
    def register(cls, host, port, server):
        with cls._lock:
            cls._servers[(str(host), int(port))] = server

    @classmethod
    def get(cls, host, port):
        with cls._lock:
            return cls._servers.get((str(host), int(port)))


class _FakeModbusServer:
    def __init__(self, host, port, no_block=True):
        self.host = str(host)
        self.port = int(port)
        self.no_block = bool(no_block)
        self.data_bank = _FakeDataBank()

    def start(self):
        _FakeModbusRegistry.register(self.host, self.port, self)

    def stop(self):
        pass


class _FakeModbusClient:
    def __init__(self, host, port):
        self.host = str(host)
        self.port = int(port)
        self.is_open = False

    def open(self):
        if _FakeModbusRegistry.get(self.host, self.port) is None:
            self.is_open = False
            return False
        self.is_open = True
        return True

    def close(self):
        self.is_open = False

    def read_holding_registers(self, address, count):
        if not self.is_open:
            return None
        server = _FakeModbusRegistry.get(self.host, self.port)
        if server is None:
            return None
        return server.data_bank.get_holding_registers(address, count)

    def write_single_register(self, address, value):
        if not self.is_open:
            return False
        server = _FakeModbusRegistry.get(self.host, self.port)
        if server is None:
            return False
        server.data_bank.set_holding_registers(address, [value])
        return True


def _empty_df_by_plant(plant_ids):
    return {plant_id: pd.DataFrame() for plant_id in plant_ids}


def _default_measurement_post_status_by_plant(plant_ids):
    return {
        plant_id: {
            "posting_enabled": False,
            "last_success": None,
            "last_attempt": None,
            "last_error": None,
            "pending_queue_count": 0,
            "oldest_pending_age_s": None,
            "last_enqueue": None,
        }
        for plant_id in plant_ids
    }


def _build_shared_data(config):
    plant_ids = tuple(config.get("PLANT_IDS", ("lib", "vrfb")))
    return {
        "manual_schedule_df_by_plant": _empty_df_by_plant(plant_ids),
        "manual_schedule_series_df_by_key": msm.default_manual_series_map(),
        "manual_schedule_merge_enabled_by_key": msm.default_manual_merge_enabled_map(default_enabled=False),
        "api_schedule_df_by_plant": _empty_df_by_plant(plant_ids),
        "transport_mode": "local",
        "scheduler_running_by_plant": {plant_id: False for plant_id in plant_ids},
        "plant_transition_by_plant": {plant_id: "stopped" for plant_id in plant_ids},
        "measurements_filename_by_plant": {plant_id: None for plant_id in plant_ids},
        "current_file_path_by_plant": {plant_id: None for plant_id in plant_ids},
        "current_file_df_by_plant": _empty_df_by_plant(plant_ids),
        "pending_rows_by_file": {},
        "measurements_df": pd.DataFrame(),
        "measurement_post_status": _default_measurement_post_status_by_plant(plant_ids),
        "api_password": None,
        "data_fetcher_status": {
            "connected": False,
            "today_fetched": False,
            "tomorrow_fetched": False,
            "today_date": None,
            "tomorrow_date": None,
            "today_points": 0,
            "tomorrow_points": 0,
            "today_points_by_plant": {plant_id: 0 for plant_id in plant_ids},
            "tomorrow_points_by_plant": {plant_id: 0 for plant_id in plant_ids},
            "last_attempt": None,
            "error": None,
        },
        "transport_switching": False,
        "lock": threading.Lock(),
        "shutdown_event": threading.Event(),
    }


class LocalRuntimeSmokeTests(unittest.TestCase):
    def test_local_mode_start_record_stop_writes_measurements_for_both_plants(self):
        with TemporaryDirectory() as tmpdir:
            with chdir(tmpdir):
                _FakeModbusRegistry.clear()
                config = load_config("/home/jaime/HIL-scheduler/config.yaml")
                config["SCHEDULER_PERIOD_S"] = 0.2
                config["MEASUREMENT_PERIOD_S"] = 0.2
                config["MEASUREMENTS_WRITE_PERIOD_S"] = 0.2
                config["PLANT_PERIOD_S"] = 0.1
                config["ISTENTORE_POST_MEASUREMENTS_IN_API_MODE"] = False

                lib_port = 5020
                vrfb_port = 5021
                config["PLANTS"]["lib"]["modbus"]["local"]["host"] = "127.0.0.1"
                config["PLANTS"]["lib"]["modbus"]["local"]["port"] = lib_port
                config["PLANTS"]["vrfb"]["modbus"]["local"]["host"] = "127.0.0.1"
                config["PLANTS"]["vrfb"]["modbus"]["local"]["port"] = vrfb_port

                shared_data = _build_shared_data(config)
                now = now_tz(config)
                schedule_index = pd.DatetimeIndex([now - pd.Timedelta(minutes=5), now + pd.Timedelta(minutes=5)])
                lib_df = pd.DataFrame(
                    {
                        "power_setpoint_kw": [120.0, 120.0],
                        "reactive_power_setpoint_kvar": [0.0, 0.0],
                    },
                    index=schedule_index,
                )
                vrfb_df = pd.DataFrame(
                    {
                        "power_setpoint_kw": [250.0, 250.0],
                        "reactive_power_setpoint_kvar": [0.0, 0.0],
                    },
                    index=schedule_index,
                )
                with shared_data["lock"]:
                    shared_data["api_schedule_df_by_plant"]["lib"] = lib_df
                    shared_data["api_schedule_df_by_plant"]["vrfb"] = vrfb_df

                with patch("plant_agent.ModbusServer", _FakeModbusServer), patch(
                    "scheduler_agent.ModbusClient",
                    _FakeModbusClient,
                ), patch("measurement_sampling.ModbusClient", _FakeModbusClient):
                    threads = [
                        threading.Thread(target=plant_agent, args=(config, shared_data), daemon=True),
                        threading.Thread(target=scheduler_agent, args=(config, shared_data), daemon=True),
                        threading.Thread(target=measurement_agent, args=(config, shared_data), daemon=True),
                    ]
                    for thread in threads:
                        thread.start()

                    time.sleep(0.6)

                    lib_enable_reg = int(config["PLANTS"]["lib"]["modbus"]["local"]["points"]["enable"]["address"])
                    vrfb_enable_reg = int(config["PLANTS"]["vrfb"]["modbus"]["local"]["points"]["enable"]["address"])
                    lib_server = _FakeModbusRegistry.get("127.0.0.1", lib_port)
                    vrfb_server = _FakeModbusRegistry.get("127.0.0.1", vrfb_port)
                    self.assertIsNotNone(lib_server)
                    self.assertIsNotNone(vrfb_server)
                    lib_server.data_bank.set_holding_registers(lib_enable_reg, [1])
                    vrfb_server.data_bank.set_holding_registers(vrfb_enable_reg, [1])

                    with shared_data["lock"]:
                        shared_data["scheduler_running_by_plant"]["lib"] = True
                        shared_data["scheduler_running_by_plant"]["vrfb"] = True
                        shared_data["measurements_filename_by_plant"]["lib"] = "data/20990101_lib.csv"
                        shared_data["measurements_filename_by_plant"]["vrfb"] = "data/20990101_vrfb.csv"

                    time.sleep(1.4)

                    with shared_data["lock"]:
                        shared_data["scheduler_running_by_plant"]["lib"] = False
                        shared_data["scheduler_running_by_plant"]["vrfb"] = False
                        shared_data["measurements_filename_by_plant"]["lib"] = None
                        shared_data["measurements_filename_by_plant"]["vrfb"] = None

                    time.sleep(0.6)
                    shared_data["shutdown_event"].set()
                    for thread in threads:
                        thread.join(timeout=5)

                lib_candidates = sorted(glob.glob("data/*_lib.csv"))
                vrfb_candidates = sorted(glob.glob("data/*_vrfb.csv"))
                self.assertTrue(lib_candidates)
                self.assertTrue(vrfb_candidates)

                lib_path = lib_candidates[-1]
                vrfb_path = vrfb_candidates[-1]
                self.assertTrue(os.path.exists(lib_path))
                self.assertTrue(os.path.exists(vrfb_path))

                lib_out = pd.read_csv(lib_path)
                vrfb_out = pd.read_csv(vrfb_path)
                self.assertGreaterEqual(len(lib_out), 3)
                self.assertGreaterEqual(len(vrfb_out), 3)

                lib_real = lib_out.dropna(subset=["battery_active_power_kw"])
                vrfb_real = vrfb_out.dropna(subset=["battery_active_power_kw"])
                self.assertFalse(lib_real.empty)
                self.assertFalse(vrfb_real.empty)
                self.assertTrue((lib_real["battery_active_power_kw"].abs() > 0).any())
                self.assertTrue((vrfb_real["battery_active_power_kw"].abs() > 0).any())

                lib_last = lib_out.iloc[-1]
                vrfb_last = vrfb_out.iloc[-1]
                self.assertTrue(all(pd.isna(lib_last[column]) for column in MEASUREMENT_VALUE_COLUMNS))
                self.assertTrue(all(pd.isna(vrfb_last[column]) for column in MEASUREMENT_VALUE_COLUMNS))


if __name__ == "__main__":
    unittest.main()
