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
        "active_schedule_source": "manual",
        "scheduler_running_by_plant": {"lib": True, "vrfb": False},
        "manual_schedule_df_by_plant": {"lib": pd.DataFrame(), "vrfb": pd.DataFrame()},
        "api_schedule_df_by_plant": {"lib": pd.DataFrame(), "vrfb": pd.DataFrame()},
    }


def _read_kw_from_register(data_bank, register):
    raw = data_bank.get_holding_registers(register, 1)[0]
    return hw_to_kw(uint16_to_int(raw))


class SchedulerSourceSwitchTests(unittest.TestCase):
    def test_manual_to_api_stale_switch_dispatches_zero(self):
        _FakeServerRegistry.clear()
        config = load_config("config.yaml")
        config["SCHEDULER_PERIOD_S"] = 0.1
        config["ISTENTORE_SCHEDULE_PERIOD_MINUTES"] = 15
        config["PLANTS"]["lib"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["lib"]["modbus"]["local"]["port"] = 5020
        config["PLANTS"]["vrfb"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["vrfb"]["modbus"]["local"]["port"] = 5021

        lib_registers = config["PLANTS"]["lib"]["modbus"]["local"]["registers"]
        lib_p_reg = int(lib_registers["p_setpoint"])
        lib_q_reg = int(lib_registers["q_setpoint"])

        lib_bank = _FakeDataBank()
        vrfb_bank = _FakeDataBank()
        _FakeServerRegistry.register("127.0.0.1", 5020, lib_bank)
        _FakeServerRegistry.register("127.0.0.1", 5021, vrfb_bank)

        now = now_tz(config)
        manual_df = pd.DataFrame(
            {
                "power_setpoint_kw": [123.4, 123.4],
                "reactive_power_setpoint_kvar": [5.0, 5.0],
            },
            index=pd.DatetimeIndex([now - pd.Timedelta(minutes=1), now + pd.Timedelta(minutes=5)]),
        )
        stale_api_df = pd.DataFrame(
            {
                "power_setpoint_kw": [777.0],
                "reactive_power_setpoint_kvar": [55.0],
            },
            index=pd.DatetimeIndex([now - pd.Timedelta(hours=2)]),
        )

        shared_data = _shared_data()
        with shared_data["lock"]:
            shared_data["manual_schedule_df_by_plant"]["lib"] = manual_df
            shared_data["api_schedule_df_by_plant"]["lib"] = stale_api_df

        with patch("scheduler_agent.ModbusClient", _FakeModbusClient):
            thread = threading.Thread(target=scheduler_agent, args=(config, shared_data), daemon=True)
            thread.start()
            try:
                time.sleep(0.45)
                manual_p = _read_kw_from_register(lib_bank, lib_p_reg)
                manual_q = _read_kw_from_register(lib_bank, lib_q_reg)
                self.assertAlmostEqual(manual_p, 123.4, places=1)
                self.assertAlmostEqual(manual_q, 5.0, places=1)

                with shared_data["lock"]:
                    shared_data["active_schedule_source"] = "api"

                time.sleep(0.45)
                api_p = _read_kw_from_register(lib_bank, lib_p_reg)
                api_q = _read_kw_from_register(lib_bank, lib_q_reg)
                self.assertAlmostEqual(api_p, 0.0, places=1)
                self.assertAlmostEqual(api_q, 0.0, places=1)
            finally:
                shared_data["shutdown_event"].set()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
