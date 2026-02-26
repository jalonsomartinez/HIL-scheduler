import threading
import time
import unittest
from unittest.mock import patch

import pandas as pd

from config_loader import load_config
from scheduler_agent import scheduler_agent
from time_utils import now_tz
from utils import hw_to_kw, uint16_to_int


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


class _FakeServerRegistry:
    _lock = threading.Lock()
    _servers = {}

    @classmethod
    def clear(cls):
        with cls._lock:
            cls._servers = {}

    @classmethod
    def register(cls, host, port, data_bank):
        with cls._lock:
            cls._servers[(str(host), int(port))] = data_bank

    @classmethod
    def get(cls, host, port):
        with cls._lock:
            return cls._servers.get((str(host), int(port)))


class _FakeModbusClient:
    def __init__(self, host, port):
        self.host = str(host)
        self.port = int(port)
        self.is_open = False

    def open(self):
        self.is_open = _FakeServerRegistry.get(self.host, self.port) is not None
        return self.is_open

    def close(self):
        self.is_open = False

    def write_single_register(self, address, value):
        if not self.is_open:
            return False
        data_bank = _FakeServerRegistry.get(self.host, self.port)
        if data_bank is None:
            return False
        data_bank.set_holding_registers(address, [value])
        return True


def _shared_data():
    return {
        "lock": threading.Lock(),
        "shutdown_event": threading.Event(),
        "transport_mode": "local",
        "scheduler_running_by_plant": {"lib": True, "vrfb": False},
        "manual_schedule_df_by_plant": {"lib": pd.DataFrame(), "vrfb": pd.DataFrame()},
        "manual_schedule_series_df_by_key": {
            "lib_p": pd.DataFrame(columns=["setpoint"]),
            "lib_q": pd.DataFrame(columns=["setpoint"]),
            "vrfb_p": pd.DataFrame(columns=["setpoint"]),
            "vrfb_q": pd.DataFrame(columns=["setpoint"]),
        },
        "manual_schedule_merge_enabled_by_key": {
            "lib_p": False,
            "lib_q": False,
            "vrfb_p": False,
            "vrfb_q": False,
        },
        "api_schedule_df_by_plant": {"lib": pd.DataFrame(), "vrfb": pd.DataFrame()},
    }


def _read_kw_from_register(data_bank, register):
    raw = data_bank.get_holding_registers(register, 1)[0]
    return hw_to_kw(uint16_to_int(raw))


class SchedulerSourceSwitchTests(unittest.TestCase):
    def test_manual_p_override_has_priority_over_api_base(self):
        _FakeServerRegistry.clear()
        config = load_config("config.yaml")
        config["SCHEDULER_PERIOD_S"] = 0.1
        config["ISTENTORE_SCHEDULE_PERIOD_MINUTES"] = 15
        config["PLANTS"]["lib"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["lib"]["modbus"]["local"]["port"] = 5020
        config["PLANTS"]["vrfb"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["vrfb"]["modbus"]["local"]["port"] = 5021

        lib_points = config["PLANTS"]["lib"]["modbus"]["local"]["points"]
        lib_p_reg = int(lib_points["p_setpoint"]["address"])
        lib_q_reg = int(lib_points["q_setpoint"]["address"])

        lib_bank = _FakeDataBank()
        vrfb_bank = _FakeDataBank()
        _FakeServerRegistry.register("127.0.0.1", 5020, lib_bank)
        _FakeServerRegistry.register("127.0.0.1", 5021, vrfb_bank)

        now = now_tz(config)
        api_df = pd.DataFrame(
            {
                "power_setpoint_kw": [200.0, 200.0],
                "reactive_power_setpoint_kvar": [12.0, 12.0],
            },
            index=pd.DatetimeIndex([now - pd.Timedelta(minutes=2), now + pd.Timedelta(minutes=5)]),
        )
        manual_p_df = pd.DataFrame(
            {"setpoint": [123.4, 123.4]},
            index=pd.DatetimeIndex([now - pd.Timedelta(minutes=1), now + pd.Timedelta(minutes=5)]),
        )

        shared_data = _shared_data()
        with shared_data["lock"]:
            shared_data["api_schedule_df_by_plant"]["lib"] = api_df
            shared_data["manual_schedule_series_df_by_key"]["lib_p"] = manual_p_df
            shared_data["manual_schedule_merge_enabled_by_key"]["lib_p"] = True

        with patch("scheduler_agent.ModbusClient", _FakeModbusClient):
            thread = threading.Thread(target=scheduler_agent, args=(config, shared_data), daemon=True)
            thread.start()
            try:
                time.sleep(0.45)
                p_val = _read_kw_from_register(lib_bank, lib_p_reg)
                q_val = _read_kw_from_register(lib_bank, lib_q_reg)
                self.assertAlmostEqual(p_val, 123.4, places=1)
                self.assertAlmostEqual(q_val, 12.0, places=1)
            finally:
                shared_data["shutdown_event"].set()
                thread.join(timeout=3)

    def test_api_stale_base_with_manual_p_override_dispatches_manual_p_and_zero_q(self):
        _FakeServerRegistry.clear()
        config = load_config("config.yaml")
        config["SCHEDULER_PERIOD_S"] = 0.1
        config["ISTENTORE_SCHEDULE_PERIOD_MINUTES"] = 15
        config["PLANTS"]["lib"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["lib"]["modbus"]["local"]["port"] = 5020
        config["PLANTS"]["vrfb"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["vrfb"]["modbus"]["local"]["port"] = 5021

        lib_points = config["PLANTS"]["lib"]["modbus"]["local"]["points"]
        lib_p_reg = int(lib_points["p_setpoint"]["address"])
        lib_q_reg = int(lib_points["q_setpoint"]["address"])

        lib_bank = _FakeDataBank()
        vrfb_bank = _FakeDataBank()
        _FakeServerRegistry.register("127.0.0.1", 5020, lib_bank)
        _FakeServerRegistry.register("127.0.0.1", 5021, vrfb_bank)

        now = now_tz(config)
        stale_api_df = pd.DataFrame(
            {"power_setpoint_kw": [777.0], "reactive_power_setpoint_kvar": [55.0]},
            index=pd.DatetimeIndex([now - pd.Timedelta(hours=2)]),
        )
        manual_p_df = pd.DataFrame(
            {"setpoint": [88.8, 88.8]},
            index=pd.DatetimeIndex([now - pd.Timedelta(minutes=1), now + pd.Timedelta(minutes=5)]),
        )

        shared_data = _shared_data()
        with shared_data["lock"]:
            shared_data["api_schedule_df_by_plant"]["lib"] = stale_api_df
            shared_data["manual_schedule_series_df_by_key"]["lib_p"] = manual_p_df
            shared_data["manual_schedule_merge_enabled_by_key"]["lib_p"] = True

        with patch("scheduler_agent.ModbusClient", _FakeModbusClient):
            thread = threading.Thread(target=scheduler_agent, args=(config, shared_data), daemon=True)
            thread.start()
            try:
                time.sleep(0.45)
                p_val = _read_kw_from_register(lib_bank, lib_p_reg)
                q_val = _read_kw_from_register(lib_bank, lib_q_reg)
                self.assertAlmostEqual(p_val, 88.8, places=1)
                self.assertAlmostEqual(q_val, 0.0, places=1)
            finally:
                shared_data["shutdown_event"].set()
                thread.join(timeout=3)

    def test_manual_p_override_terminal_end_in_past_does_not_override_api_base(self):
        _FakeServerRegistry.clear()
        config = load_config("config.yaml")
        config["SCHEDULER_PERIOD_S"] = 0.1
        config["ISTENTORE_SCHEDULE_PERIOD_MINUTES"] = 15
        config["PLANTS"]["lib"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["lib"]["modbus"]["local"]["port"] = 5020
        config["PLANTS"]["vrfb"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["vrfb"]["modbus"]["local"]["port"] = 5021

        lib_points = config["PLANTS"]["lib"]["modbus"]["local"]["points"]
        lib_p_reg = int(lib_points["p_setpoint"]["address"])
        lib_q_reg = int(lib_points["q_setpoint"]["address"])

        lib_bank = _FakeDataBank()
        vrfb_bank = _FakeDataBank()
        _FakeServerRegistry.register("127.0.0.1", 5020, lib_bank)
        _FakeServerRegistry.register("127.0.0.1", 5021, vrfb_bank)

        now = now_tz(config)
        api_df = pd.DataFrame(
            {"power_setpoint_kw": [200.0], "reactive_power_setpoint_kvar": [12.0]},
            index=pd.DatetimeIndex([now - pd.Timedelta(minutes=2)]),
        )
        manual_p_df = pd.DataFrame(
            {"setpoint": [123.4, 123.4]},
            index=pd.DatetimeIndex([now - pd.Timedelta(minutes=30), now - pd.Timedelta(minutes=1)]),
        )

        shared_data = _shared_data()
        with shared_data["lock"]:
            shared_data["api_schedule_df_by_plant"]["lib"] = api_df
            shared_data["manual_schedule_series_df_by_key"]["lib_p"] = manual_p_df
            shared_data["manual_schedule_merge_enabled_by_key"]["lib_p"] = True

        with patch("scheduler_agent.ModbusClient", _FakeModbusClient):
            thread = threading.Thread(target=scheduler_agent, args=(config, shared_data), daemon=True)
            thread.start()
            try:
                time.sleep(0.45)
                p_val = _read_kw_from_register(lib_bank, lib_p_reg)
                q_val = _read_kw_from_register(lib_bank, lib_q_reg)
                self.assertAlmostEqual(p_val, 200.0, places=1)
                self.assertAlmostEqual(q_val, 12.0, places=1)
            finally:
                shared_data["shutdown_event"].set()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
