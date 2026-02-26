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
        with self._lock:
            for offset, value in enumerate(values):
                self._registers[int(address) + offset] = int(value)

    def get_holding_registers(self, address, count):
        with self._lock:
            return [self._registers.get(int(address) + offset, 0) for offset in range(int(count))]


class _Registry:
    _lock = threading.Lock()
    _servers = {}

    @classmethod
    def clear(cls):
        with cls._lock:
            cls._servers = {}

    @classmethod
    def register(cls, host, port, bank):
        with cls._lock:
            cls._servers[(str(host), int(port))] = bank

    @classmethod
    def get(cls, host, port):
        with cls._lock:
            return cls._servers.get((str(host), int(port)))


class _FlakyOnceModbusClient:
    write_counts = {}
    failed_once_keys = set()

    @classmethod
    def reset(cls):
        cls.write_counts = {}
        cls.failed_once_keys = set()

    def __init__(self, host, port):
        self.host = str(host)
        self.port = int(port)
        self.is_open = False

    def open(self):
        self.is_open = _Registry.get(self.host, self.port) is not None
        return self.is_open

    def close(self):
        self.is_open = False

    def write_single_register(self, address, value):
        if not self.is_open:
            return False
        key = (self.host, self.port, int(address))
        self.__class__.write_counts[key] = int(self.__class__.write_counts.get(key, 0)) + 1
        # Fail only the first LIB p_setpoint write.
        if int(address) == 86 and key not in self.__class__.failed_once_keys:
            self.__class__.failed_once_keys.add(key)
            return False
        bank = _Registry.get(self.host, self.port)
        if bank is None:
            return False
        bank.set_holding_registers(address, [value])
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
        "manual_schedule_merge_enabled_by_key": {"lib_p": False, "lib_q": False, "vrfb_p": False, "vrfb_q": False},
        "api_schedule_df_by_plant": {"lib": pd.DataFrame(), "vrfb": pd.DataFrame()},
        "dispatch_write_status_by_plant": {"lib": {"sending_enabled": False}, "vrfb": {"sending_enabled": False}},
    }


def _read_kw(bank, register):
    raw = bank.get_holding_registers(register, 1)[0]
    return hw_to_kw(uint16_to_int(raw))


class SchedulerDispatchWriteStatusTests(unittest.TestCase):
    def test_scheduler_retries_failed_write_and_publishes_dispatch_status(self):
        _Registry.clear()
        _FlakyOnceModbusClient.reset()
        config = load_config("config.yaml")
        config["SCHEDULER_PERIOD_S"] = 0.1
        config["ISTENTORE_SCHEDULE_PERIOD_MINUTES"] = 15
        config["PLANTS"]["lib"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["lib"]["modbus"]["local"]["port"] = 5020
        config["PLANTS"]["vrfb"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["vrfb"]["modbus"]["local"]["port"] = 5021

        lib_points = config["PLANTS"]["lib"]["modbus"]["local"]["points"]
        p_reg = int(lib_points["p_setpoint"]["address"])
        q_reg = int(lib_points["q_setpoint"]["address"])

        lib_bank = _FakeDataBank()
        vrfb_bank = _FakeDataBank()
        _Registry.register("127.0.0.1", 5020, lib_bank)
        _Registry.register("127.0.0.1", 5021, vrfb_bank)

        now = now_tz(config)
        api_df = pd.DataFrame(
            {
                "power_setpoint_kw": [42.0],
                "reactive_power_setpoint_kvar": [5.0],
            },
            index=pd.DatetimeIndex([now - pd.Timedelta(minutes=1)]),
        )
        shared_data = _shared_data()
        with shared_data["lock"]:
            shared_data["api_schedule_df_by_plant"]["lib"] = api_df

        with patch("scheduler_agent.ModbusClient", _FlakyOnceModbusClient):
            thread = threading.Thread(target=scheduler_agent, args=(config, shared_data), daemon=True)
            thread.start()
            try:
                time.sleep(0.55)
            finally:
                shared_data["shutdown_event"].set()
                thread.join(timeout=3)

        self.assertAlmostEqual(_read_kw(lib_bank, p_reg), 42.0, places=1)
        self.assertAlmostEqual(_read_kw(lib_bank, q_reg), 5.0, places=1)
        self.assertGreaterEqual(_FlakyOnceModbusClient.write_counts.get(("127.0.0.1", 5020, p_reg), 0), 2)

        dispatch_state = dict(shared_data["dispatch_write_status_by_plant"]["lib"])
        self.assertTrue(dispatch_state["sending_enabled"])
        self.assertEqual(dispatch_state["last_attempt_source"], "scheduler")
        self.assertEqual(dispatch_state["last_attempt_status"], "ok")
        self.assertIsNotNone(dispatch_state["last_success_at"])
        self.assertAlmostEqual(float(dispatch_state["last_success_p_kw"]), 42.0, places=3)
        self.assertAlmostEqual(float(dispatch_state["last_success_q_kvar"]), 5.0, places=3)


if __name__ == "__main__":
    unittest.main()
