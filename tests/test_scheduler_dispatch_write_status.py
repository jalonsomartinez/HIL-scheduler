import threading
import time
import unittest
from unittest.mock import patch

import pandas as pd

from config_loader import load_config
from modbus.codec import encode_point_internal_words, read_point_internal
import scheduling.manual_schedule_manager as msm
from scheduling.agent import scheduler_agent
from time_utils import now_tz
from modbus.legacy_scaling import hw_to_kw, uint16_to_int


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


class _BankClient:
    def __init__(self, bank):
        self.bank = bank

    def read_holding_registers(self, address, count):
        return self.bank.get_holding_registers(address, count)


class _FlakyOnceModbusClient:
    write_counts = {}
    failed_once_keys = set()
    write_log = []

    @classmethod
    def reset(cls):
        cls.write_counts = {}
        cls.failed_once_keys = set()
        cls.write_log = []

    def __init__(self, host, port):
        self.host = str(host)
        self.port = int(port)
        self.is_open = False

    def open(self):
        self.is_open = _Registry.get(self.host, self.port) is not None
        return self.is_open

    def close(self):
        self.is_open = False

    def read_holding_registers(self, address, count):
        if not self.is_open:
            return None
        bank = _Registry.get(self.host, self.port)
        if bank is None:
            return None
        return bank.get_holding_registers(address, count)

    def write_single_register(self, address, value):
        if not self.is_open:
            return False
        key = (self.host, self.port, int(address))
        self.__class__.write_counts[key] = int(self.__class__.write_counts.get(key, 0)) + 1
        self.__class__.write_log.append((self.host, self.port, int(address), int(value)))
        # Fail only the first LIB p_setpoint write.
        if int(address) == 86 and key not in self.__class__.failed_once_keys:
            self.__class__.failed_once_keys.add(key)
            return False
        bank = _Registry.get(self.host, self.port)
        if bank is None:
            return False
        bank.set_holding_registers(address, [value])
        return True


class _CountingModbusClient:
    write_counts = {}
    write_log = []

    @classmethod
    def reset(cls):
        cls.write_counts = {}
        cls.write_log = []

    def __init__(self, host, port):
        self.host = str(host)
        self.port = int(port)
        self.is_open = False

    def open(self):
        self.is_open = _Registry.get(self.host, self.port) is not None
        return self.is_open

    def close(self):
        self.is_open = False

    def read_holding_registers(self, address, count):
        if not self.is_open:
            return None
        bank = _Registry.get(self.host, self.port)
        if bank is None:
            return None
        return bank.get_holding_registers(address, count)

    def write_single_register(self, address, value):
        if not self.is_open:
            return False
        key = (self.host, self.port, int(address))
        self.__class__.write_counts[key] = int(self.__class__.write_counts.get(key, 0)) + 1
        self.__class__.write_log.append((self.host, self.port, int(address), int(value)))
        bank = _Registry.get(self.host, self.port)
        if bank is None:
            return False
        bank.set_holding_registers(address, [value])
        return True


class _ReadbackFailingModbusClient(_CountingModbusClient):
    failed_read_addresses = set()

    @classmethod
    def reset(cls):
        super().reset()
        cls.failed_read_addresses = set()

    def read_holding_registers(self, address, count):
        if int(address) in self.__class__.failed_read_addresses:
            return None
        return super().read_holding_registers(address, count)


class _TriggerFlakyOnceModbusClient(_CountingModbusClient):
    failed_once_addresses = set()
    failed_once_keys = set()

    @classmethod
    def reset(cls):
        super().reset()
        cls.failed_once_addresses = set()
        cls.failed_once_keys = set()

    def write_single_register(self, address, value):
        key = (self.host, self.port, int(address))
        if int(address) in self.__class__.failed_once_addresses and key not in self.__class__.failed_once_keys:
            self.__class__.failed_once_keys.add(key)
            self.__class__.write_counts[key] = int(self.__class__.write_counts.get(key, 0)) + 1
            self.__class__.write_log.append((self.host, self.port, int(address), int(value)))
            return False
        return super().write_single_register(address, value)


def _shared_data():
    return {
        "lock": threading.Lock(),
        "shutdown_event": threading.Event(),
        "transport_mode": "local",
        "scheduler_running_by_plant": {"lib": True, "vrfb": False},
        "manual_schedule_df_by_plant": {"lib": pd.DataFrame(), "vrfb": pd.DataFrame()},
        "manual_schedule_series_df_by_key": msm.default_manual_series_map(),
        "manual_schedule_merge_enabled_by_key": msm.default_manual_merge_enabled_map(default_enabled=False),
        "reactive_control_mode_by_plant": {"lib": 1, "vrfb": 1},
        "api_schedule_df_by_plant": {"lib": pd.DataFrame(), "vrfb": pd.DataFrame()},
        "dispatch_write_status_by_plant": {"lib": {"sending_enabled": False}, "vrfb": {"sending_enabled": False}},
    }


def _read_kw(bank, register):
    raw = bank.get_holding_registers(register, 1)[0]
    return hw_to_kw(uint16_to_int(raw))


def _seed_setpoints(bank, endpoint_cfg, p_kw, q_kvar):
    points = endpoint_cfg["points"]
    p_reg = int(points["p_setpoint"]["address"])
    q_reg = int(points["q_setpoint"]["address"])
    bank.set_holding_registers(p_reg, encode_point_internal_words(endpoint_cfg, "p_setpoint", p_kw))
    bank.set_holding_registers(q_reg, encode_point_internal_words(endpoint_cfg, "q_setpoint", q_kvar))


def _seed_q_control_mode_if_configured(bank, endpoint_cfg, mode):
    points = endpoint_cfg["points"]
    if "q_control_mode" not in points:
        return
    point = points["q_control_mode"]
    bank.set_holding_registers(point["address"], encode_point_internal_words(endpoint_cfg, "q_control_mode", mode))


def _read_point_internal_from_bank(bank, endpoint_cfg, point_name):
    return read_point_internal(_BankClient(bank), endpoint_cfg, point_name)


class SchedulerDispatchWriteStatusTests(unittest.TestCase):
    def test_scheduler_writes_equal_phase_split_for_per_phase_endpoint(self):
        _Registry.clear()
        _CountingModbusClient.reset()
        config = load_config("config.yaml")
        config["SCHEDULER_PERIOD_S"] = 0.1
        config["ISTENTORE_SCHEDULE_PERIOD_MINUTES"] = 15
        config["PLANTS"]["lib"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["lib"]["modbus"]["local"]["port"] = 5020
        config["PLANTS"]["vrfb"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["vrfb"]["modbus"]["local"]["port"] = 5021

        lib_endpoint = config["PLANTS"]["lib"]["modbus"]["local"]
        lib_points = lib_endpoint["points"]
        lib_points.pop("p_setpoint", None)
        lib_points.pop("q_setpoint", None)
        lib_points["p_u_setpoint"] = {"name": "p_u_setpoint", "address": 86, "format": "int16", "word_count": 1, "byte_count": 2, "access": "rw", "unit": "kw", "eng_per_count": 0.1}
        lib_points["p_v_setpoint"] = {"name": "p_v_setpoint", "address": 87, "format": "int16", "word_count": 1, "byte_count": 2, "access": "rw", "unit": "kw", "eng_per_count": 0.1}
        lib_points["p_w_setpoint"] = {"name": "p_w_setpoint", "address": 88, "format": "int16", "word_count": 1, "byte_count": 2, "access": "rw", "unit": "kw", "eng_per_count": 0.1}
        lib_points["q_u_setpoint"] = {"name": "q_u_setpoint", "address": 89, "format": "int16", "word_count": 1, "byte_count": 2, "access": "rw", "unit": "kvar", "eng_per_count": 0.1}
        lib_points["q_v_setpoint"] = {"name": "q_v_setpoint", "address": 90, "format": "int16", "word_count": 1, "byte_count": 2, "access": "rw", "unit": "kvar", "eng_per_count": 0.1}
        lib_points["q_w_setpoint"] = {"name": "q_w_setpoint", "address": 91, "format": "int16", "word_count": 1, "byte_count": 2, "access": "rw", "unit": "kvar", "eng_per_count": 0.1}

        lib_bank = _FakeDataBank()
        vrfb_bank = _FakeDataBank()
        _Registry.register("127.0.0.1", 5020, lib_bank)
        _Registry.register("127.0.0.1", 5021, vrfb_bank)

        now = now_tz(config)
        api_df = pd.DataFrame(
            {"power_setpoint_kw": [42.0], "reactive_power_setpoint_kvar": [6.0]},
            index=pd.DatetimeIndex([now - pd.Timedelta(minutes=1)]),
        )
        shared_data = _shared_data()
        with shared_data["lock"]:
            shared_data["api_schedule_df_by_plant"]["lib"] = api_df

        with patch("scheduling.agent.ModbusClient", _CountingModbusClient):
            thread = threading.Thread(target=scheduler_agent, args=(config, shared_data), daemon=True)
            thread.start()
            try:
                time.sleep(0.35)
            finally:
                shared_data["shutdown_event"].set()
                thread.join(timeout=3)

        self.assertAlmostEqual(_read_point_internal_from_bank(lib_bank, lib_endpoint, "p_u_setpoint"), 14.0, places=6)
        self.assertAlmostEqual(_read_point_internal_from_bank(lib_bank, lib_endpoint, "p_v_setpoint"), 14.0, places=6)
        self.assertAlmostEqual(_read_point_internal_from_bank(lib_bank, lib_endpoint, "p_w_setpoint"), 14.0, places=6)
        self.assertAlmostEqual(_read_point_internal_from_bank(lib_bank, lib_endpoint, "q_u_setpoint"), 2.0, places=6)
        self.assertAlmostEqual(_read_point_internal_from_bank(lib_bank, lib_endpoint, "q_v_setpoint"), 2.0, places=6)
        self.assertAlmostEqual(_read_point_internal_from_bank(lib_bank, lib_endpoint, "q_w_setpoint"), 2.0, places=6)
        dispatch_state = dict(shared_data["dispatch_write_status_by_plant"]["lib"])
        self.assertEqual(dispatch_state["last_scheduler_context"]["setpoint_mode"], "per_phase")

    def test_scheduler_clamps_aggregate_setpoints_before_write_and_records_metadata(self):
        _Registry.clear()
        _CountingModbusClient.reset()
        config = load_config("config.yaml")
        config["SCHEDULER_PERIOD_S"] = 0.1
        config["ISTENTORE_SCHEDULE_PERIOD_MINUTES"] = 15
        config["PLANTS"]["lib"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["lib"]["modbus"]["local"]["port"] = 5020
        config["PLANTS"]["vrfb"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["vrfb"]["modbus"]["local"]["port"] = 5021
        config["PLANTS"]["lib"]["model"]["power_limits"] = {
            "p_max_kw": 10.0,
            "p_min_kw": -10.0,
            "q_max_kvar": 2.0,
            "q_min_kvar": -2.0,
        }

        lib_endpoint = config["PLANTS"]["lib"]["modbus"]["local"]
        lib_points = lib_endpoint["points"]
        p_reg = int(lib_points["p_setpoint"]["address"])
        q_reg = int(lib_points["q_setpoint"]["address"])

        lib_bank = _FakeDataBank()
        vrfb_bank = _FakeDataBank()
        _Registry.register("127.0.0.1", 5020, lib_bank)
        _Registry.register("127.0.0.1", 5021, vrfb_bank)

        now = now_tz(config)
        api_df = pd.DataFrame(
            {"power_setpoint_kw": [42.0], "reactive_power_setpoint_kvar": [5.0]},
            index=pd.DatetimeIndex([now - pd.Timedelta(minutes=1)]),
        )
        shared_data = _shared_data()
        with shared_data["lock"]:
            shared_data["api_schedule_df_by_plant"]["lib"] = api_df

        with patch("scheduling.agent.ModbusClient", _CountingModbusClient):
            thread = threading.Thread(target=scheduler_agent, args=(config, shared_data), daemon=True)
            thread.start()
            try:
                time.sleep(0.35)
            finally:
                shared_data["shutdown_event"].set()
                thread.join(timeout=3)

        self.assertAlmostEqual(_read_kw(lib_bank, p_reg), 10.0, places=1)
        self.assertAlmostEqual(_read_kw(lib_bank, q_reg), 2.0, places=1)
        dispatch_state = dict(shared_data["dispatch_write_status_by_plant"]["lib"])
        self.assertAlmostEqual(float(dispatch_state["last_success_p_kw"]), 10.0, places=3)
        self.assertAlmostEqual(float(dispatch_state["last_success_q_kvar"]), 2.0, places=3)
        scheduler_ctx = dict(dispatch_state.get("last_scheduler_context") or {})
        self.assertTrue(scheduler_ctx.get("any_clamped"))
        self.assertTrue(scheduler_ctx.get("p_clamped"))
        self.assertTrue(scheduler_ctx.get("q_clamped"))
        self.assertAlmostEqual(float(scheduler_ctx.get("requested_p_kw")), 42.0, places=3)
        self.assertAlmostEqual(float(scheduler_ctx.get("requested_q_kvar")), 5.0, places=3)
        self.assertAlmostEqual(float(scheduler_ctx.get("applied_p_kw")), 10.0, places=3)
        self.assertAlmostEqual(float(scheduler_ctx.get("applied_q_kvar")), 2.0, places=3)

    def test_scheduler_writes_q_control_mode_one_when_voltage_mode_inactive(self):
        _Registry.clear()
        _CountingModbusClient.reset()
        config = load_config("config.yaml")
        config["SCHEDULER_PERIOD_S"] = 0.1
        config["ISTENTORE_SCHEDULE_PERIOD_MINUTES"] = 15
        config["PLANTS"]["lib"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["lib"]["modbus"]["local"]["port"] = 5020
        config["PLANTS"]["vrfb"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["vrfb"]["modbus"]["local"]["port"] = 5021
        config["PLANTS"]["lib"]["model"]["voltage_control_droop_pu"] = 0.05

        lib_endpoint = config["PLANTS"]["lib"]["modbus"]["local"]
        lib_points = lib_endpoint["points"]
        lib_points["q_control_mode"] = {
            "name": "q_control_mode",
            "address": 96,
            "format": "uint16",
            "word_count": 1,
            "byte_count": 2,
            "access": "rw",
            "unit": "raw",
            "eng_per_count": 1.0,
        }

        lib_bank = _FakeDataBank()
        vrfb_bank = _FakeDataBank()
        _Registry.register("127.0.0.1", 5020, lib_bank)
        _Registry.register("127.0.0.1", 5021, vrfb_bank)

        now = now_tz(config)
        api_df = pd.DataFrame(
            {"power_setpoint_kw": [42.0], "reactive_power_setpoint_kvar": [5.0]},
            index=pd.DatetimeIndex([now - pd.Timedelta(minutes=1)]),
        )
        shared_data = _shared_data()
        with shared_data["lock"]:
            shared_data["api_schedule_df_by_plant"]["lib"] = api_df

        with patch("scheduling.agent.ModbusClient", _CountingModbusClient):
            thread = threading.Thread(target=scheduler_agent, args=(config, shared_data), daemon=True)
            thread.start()
            try:
                time.sleep(0.35)
            finally:
                shared_data["shutdown_event"].set()
                thread.join(timeout=3)

        self.assertEqual(lib_bank.get_holding_registers(96, 1)[0], 1)
        scheduler_ctx = dict(shared_data["dispatch_write_status_by_plant"]["lib"].get("last_scheduler_context") or {})
        self.assertEqual(int(scheduler_ctx.get("reactive_control_mode")), 1)
        self.assertFalse(bool(scheduler_ctx.get("voltage_mode_active")))

    def test_scheduler_voltage_mode_computes_q_from_voltage_and_writes_mode_three(self):
        _Registry.clear()
        _CountingModbusClient.reset()
        config = load_config("config.yaml")
        config["SCHEDULER_PERIOD_S"] = 0.1
        config["ISTENTORE_SCHEDULE_PERIOD_MINUTES"] = 15
        config["PLANTS"]["lib"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["lib"]["modbus"]["local"]["port"] = 5020
        config["PLANTS"]["vrfb"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["vrfb"]["modbus"]["local"]["port"] = 5021
        config["PLANTS"]["lib"]["model"]["voltage_control_droop_pu"] = 0.05

        lib_endpoint = config["PLANTS"]["lib"]["modbus"]["local"]
        lib_points = lib_endpoint["points"]
        q_reg = int(lib_points["q_setpoint"]["address"])
        v_poi_reg = int(lib_points["v_poi"]["address"])
        lib_points["q_control_mode"] = {
            "name": "q_control_mode",
            "address": 96,
            "format": "uint16",
            "word_count": 1,
            "byte_count": 2,
            "access": "rw",
            "unit": "raw",
            "eng_per_count": 1.0,
        }

        lib_bank = _FakeDataBank()
        vrfb_bank = _FakeDataBank()
        _Registry.register("127.0.0.1", 5020, lib_bank)
        _Registry.register("127.0.0.1", 5021, vrfb_bank)
        lib_bank.set_holding_registers(v_poi_reg, encode_point_internal_words(lib_endpoint, "v_poi", 19.8))

        now = now_tz(config)
        api_df = pd.DataFrame(
            {"power_setpoint_kw": [42.0], "reactive_power_setpoint_kvar": [5.0]},
            index=pd.DatetimeIndex([now - pd.Timedelta(minutes=1)]),
        )
        shared_data = _shared_data()
        with shared_data["lock"]:
            shared_data["api_schedule_df_by_plant"]["lib"] = api_df
            shared_data["reactive_control_mode_by_plant"]["lib"] = 3

        with patch("scheduling.agent.ModbusClient", _CountingModbusClient):
            thread = threading.Thread(target=scheduler_agent, args=(config, shared_data), daemon=True)
            thread.start()
            try:
                time.sleep(0.35)
            finally:
                shared_data["shutdown_event"].set()
                thread.join(timeout=3)

        self.assertEqual(lib_bank.get_holding_registers(96, 1)[0], 3)
        self.assertAlmostEqual(_read_kw(lib_bank, q_reg), 120.0, places=1)
        scheduler_ctx = dict(shared_data["dispatch_write_status_by_plant"]["lib"].get("last_scheduler_context") or {})
        self.assertEqual(int(scheduler_ctx.get("reactive_control_mode")), 3)
        self.assertTrue(bool(scheduler_ctx.get("voltage_mode_active")))
        self.assertAlmostEqual(float(scheduler_ctx.get("voltage_setpoint_pu")), 1.0, places=6)
        self.assertAlmostEqual(float(scheduler_ctx.get("measured_v_poi_pu")), 0.99, places=6)

    def test_scheduler_uses_digital_twin_voltage_setpoint_when_manual_voltage_is_missing(self):
        _Registry.clear()
        _CountingModbusClient.reset()
        config = load_config("config.yaml")
        config["SCHEDULER_PERIOD_S"] = 0.1
        config["ISTENTORE_SCHEDULE_PERIOD_MINUTES"] = 15
        config["PLANTS"]["lib"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["lib"]["modbus"]["local"]["port"] = 5020
        config["PLANTS"]["vrfb"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["vrfb"]["modbus"]["local"]["port"] = 5021
        config["PLANTS"]["lib"]["model"]["voltage_control_droop_pu"] = 0.05

        lib_endpoint = config["PLANTS"]["lib"]["modbus"]["local"]
        lib_points = lib_endpoint["points"]
        q_reg = int(lib_points["q_setpoint"]["address"])
        v_poi_reg = int(lib_points["v_poi"]["address"])
        lib_points["q_control_mode"] = {
            "name": "q_control_mode",
            "address": 96,
            "format": "uint16",
            "word_count": 1,
            "byte_count": 2,
            "access": "rw",
            "unit": "raw",
            "eng_per_count": 1.0,
        }

        lib_bank = _FakeDataBank()
        vrfb_bank = _FakeDataBank()
        _Registry.register("127.0.0.1", 5020, lib_bank)
        _Registry.register("127.0.0.1", 5021, vrfb_bank)
        lib_bank.set_holding_registers(v_poi_reg, encode_point_internal_words(lib_endpoint, "v_poi", 19.8))

        now = now_tz(config)
        api_df = pd.DataFrame(
            {"power_setpoint_kw": [42.0], "reactive_power_setpoint_kvar": [5.0]},
            index=pd.DatetimeIndex([now - pd.Timedelta(minutes=1)]),
        )
        shared_data = _shared_data()
        with shared_data["lock"]:
            shared_data["api_schedule_df_by_plant"]["lib"] = api_df
            shared_data["reactive_control_mode_by_plant"]["lib"] = 3
            shared_data["grid_map_runtime"] = {
                "stale": False,
                "summary": {
                    "battery_voltage_pu": 0.05,
                    "min_voltage_pu": 0.97,
                    "max_voltage_pu": 1.01,
                },
            }

        with patch("scheduling.agent.ModbusClient", _CountingModbusClient):
            thread = threading.Thread(target=scheduler_agent, args=(config, shared_data), daemon=True)
            thread.start()
            try:
                time.sleep(0.35)
            finally:
                shared_data["shutdown_event"].set()
                thread.join(timeout=3)

        self.assertEqual(lib_bank.get_holding_registers(96, 1)[0], 3)
        self.assertAlmostEqual(_read_kw(lib_bank, q_reg), -600.0, places=1)
        scheduler_ctx = dict(shared_data["dispatch_write_status_by_plant"]["lib"].get("last_scheduler_context") or {})
        self.assertAlmostEqual(float(scheduler_ctx.get("voltage_setpoint_pu")), 0.9, places=6)

    def test_scheduler_clamps_total_before_per_phase_split(self):
        _Registry.clear()
        _CountingModbusClient.reset()
        config = load_config("config.yaml")
        config["SCHEDULER_PERIOD_S"] = 0.1
        config["ISTENTORE_SCHEDULE_PERIOD_MINUTES"] = 15
        config["PLANTS"]["lib"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["lib"]["modbus"]["local"]["port"] = 5020
        config["PLANTS"]["vrfb"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["vrfb"]["modbus"]["local"]["port"] = 5021
        config["PLANTS"]["lib"]["model"]["power_limits"] = {
            "p_max_kw": 30.0,
            "p_min_kw": -30.0,
            "q_max_kvar": 3.0,
            "q_min_kvar": -3.0,
        }

        lib_endpoint = config["PLANTS"]["lib"]["modbus"]["local"]
        lib_points = lib_endpoint["points"]
        lib_points.pop("p_setpoint", None)
        lib_points.pop("q_setpoint", None)
        lib_points["p_u_setpoint"] = {"name": "p_u_setpoint", "address": 86, "format": "int16", "word_count": 1, "byte_count": 2, "access": "rw", "unit": "kw", "eng_per_count": 0.1}
        lib_points["p_v_setpoint"] = {"name": "p_v_setpoint", "address": 87, "format": "int16", "word_count": 1, "byte_count": 2, "access": "rw", "unit": "kw", "eng_per_count": 0.1}
        lib_points["p_w_setpoint"] = {"name": "p_w_setpoint", "address": 88, "format": "int16", "word_count": 1, "byte_count": 2, "access": "rw", "unit": "kw", "eng_per_count": 0.1}
        lib_points["q_u_setpoint"] = {"name": "q_u_setpoint", "address": 89, "format": "int16", "word_count": 1, "byte_count": 2, "access": "rw", "unit": "kvar", "eng_per_count": 0.1}
        lib_points["q_v_setpoint"] = {"name": "q_v_setpoint", "address": 90, "format": "int16", "word_count": 1, "byte_count": 2, "access": "rw", "unit": "kvar", "eng_per_count": 0.1}
        lib_points["q_w_setpoint"] = {"name": "q_w_setpoint", "address": 91, "format": "int16", "word_count": 1, "byte_count": 2, "access": "rw", "unit": "kvar", "eng_per_count": 0.1}

        lib_bank = _FakeDataBank()
        vrfb_bank = _FakeDataBank()
        _Registry.register("127.0.0.1", 5020, lib_bank)
        _Registry.register("127.0.0.1", 5021, vrfb_bank)

        now = now_tz(config)
        api_df = pd.DataFrame(
            {"power_setpoint_kw": [42.0], "reactive_power_setpoint_kvar": [6.0]},
            index=pd.DatetimeIndex([now - pd.Timedelta(minutes=1)]),
        )
        shared_data = _shared_data()
        with shared_data["lock"]:
            shared_data["api_schedule_df_by_plant"]["lib"] = api_df

        with patch("scheduling.agent.ModbusClient", _CountingModbusClient):
            thread = threading.Thread(target=scheduler_agent, args=(config, shared_data), daemon=True)
            thread.start()
            try:
                time.sleep(0.35)
            finally:
                shared_data["shutdown_event"].set()
                thread.join(timeout=3)

        self.assertAlmostEqual(_read_point_internal_from_bank(lib_bank, lib_endpoint, "p_u_setpoint"), 10.0, places=6)
        self.assertAlmostEqual(_read_point_internal_from_bank(lib_bank, lib_endpoint, "p_v_setpoint"), 10.0, places=6)
        self.assertAlmostEqual(_read_point_internal_from_bank(lib_bank, lib_endpoint, "p_w_setpoint"), 10.0, places=6)
        self.assertAlmostEqual(_read_point_internal_from_bank(lib_bank, lib_endpoint, "q_u_setpoint"), 1.0, places=6)
        self.assertAlmostEqual(_read_point_internal_from_bank(lib_bank, lib_endpoint, "q_v_setpoint"), 1.0, places=6)
        self.assertAlmostEqual(_read_point_internal_from_bank(lib_bank, lib_endpoint, "q_w_setpoint"), 1.0, places=6)
        dispatch_state = dict(shared_data["dispatch_write_status_by_plant"]["lib"])
        scheduler_ctx = dict(dispatch_state.get("last_scheduler_context") or {})
        self.assertEqual(scheduler_ctx.get("setpoint_mode"), "per_phase")
        self.assertTrue(scheduler_ctx.get("any_clamped"))
        self.assertAlmostEqual(float(scheduler_ctx.get("applied_p_kw")), 30.0, places=3)
        self.assertAlmostEqual(float(scheduler_ctx.get("applied_q_kvar")), 3.0, places=3)

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

        with patch("scheduling.agent.ModbusClient", _FlakyOnceModbusClient):
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
        scheduler_ctx = dict(dispatch_state.get("last_scheduler_context") or {})
        self.assertEqual(scheduler_ctx.get("readback_compare_mode"), "register_exact")
        self.assertEqual(scheduler_ctx.get("setpoint_mode"), "aggregate")
        self.assertEqual(scheduler_ctx.get("p_compare_source"), "readback")
        self.assertEqual(scheduler_ctx.get("q_compare_source"), "readback")
        self.assertTrue(scheduler_ctx.get("p_readback_ok"))
        self.assertTrue(scheduler_ctx.get("q_readback_ok"))

    @patch("modbus.setpoint_io._sleep")
    def test_scheduler_pulses_trigger_after_setpoint_write_when_configured(self, sleep_mock):
        _Registry.clear()
        _CountingModbusClient.reset()
        config = load_config("config.yaml")
        config["SCHEDULER_PERIOD_S"] = 0.1
        config["ISTENTORE_SCHEDULE_PERIOD_MINUTES"] = 15
        config["PLANTS"]["lib"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["lib"]["modbus"]["local"]["port"] = 5020
        config["PLANTS"]["vrfb"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["vrfb"]["modbus"]["local"]["port"] = 5021

        lib_endpoint = config["PLANTS"]["lib"]["modbus"]["local"]
        lib_points = lib_endpoint["points"]
        p_reg = int(lib_points["p_setpoint"]["address"])
        q_reg = int(lib_points["q_setpoint"]["address"])
        trigger_reg = 95
        lib_points["trigger"] = {
            "name": "trigger",
            "address": trigger_reg,
            "format": "uint16",
            "word_count": 1,
            "byte_count": 2,
            "access": "w",
            "unit": "raw",
            "eng_per_count": 1.0,
        }

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

        with patch("scheduling.agent.ModbusClient", _CountingModbusClient):
            thread = threading.Thread(target=scheduler_agent, args=(config, shared_data), daemon=True)
            thread.start()
            try:
                time.sleep(0.35)
            finally:
                shared_data["shutdown_event"].set()
                thread.join(timeout=3)

        self.assertAlmostEqual(_read_kw(lib_bank, p_reg), 42.0, places=1)
        self.assertAlmostEqual(_read_kw(lib_bank, q_reg), 5.0, places=1)
        self.assertEqual(lib_bank.get_holding_registers(trigger_reg, 1)[0], 0)
        self.assertEqual(_CountingModbusClient.write_counts.get(("127.0.0.1", 5020, trigger_reg), 0), 2)
        self.assertEqual(sleep_mock.call_count, 2)

    @patch("modbus.setpoint_io._sleep")
    def test_scheduler_skips_trigger_when_readback_already_matches_target(self, sleep_mock):
        _Registry.clear()
        _CountingModbusClient.reset()
        config = load_config("config.yaml")
        config["SCHEDULER_PERIOD_S"] = 0.1
        config["ISTENTORE_SCHEDULE_PERIOD_MINUTES"] = 15
        config["PLANTS"]["lib"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["lib"]["modbus"]["local"]["port"] = 5020
        config["PLANTS"]["vrfb"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["vrfb"]["modbus"]["local"]["port"] = 5021

        lib_endpoint = config["PLANTS"]["lib"]["modbus"]["local"]
        lib_points = lib_endpoint["points"]
        p_reg = int(lib_points["p_setpoint"]["address"])
        q_reg = int(lib_points["q_setpoint"]["address"])
        trigger_reg = 95
        lib_points["trigger"] = {
            "name": "trigger",
            "address": trigger_reg,
            "format": "uint16",
            "word_count": 1,
            "byte_count": 2,
            "access": "w",
            "unit": "raw",
            "eng_per_count": 1.0,
        }

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
        _seed_setpoints(lib_bank, lib_endpoint, 42.0, 5.0)
        _seed_q_control_mode_if_configured(lib_bank, lib_endpoint, 1)

        shared_data = _shared_data()
        with shared_data["lock"]:
            shared_data["api_schedule_df_by_plant"]["lib"] = api_df

        with patch("scheduling.agent.ModbusClient", _CountingModbusClient):
            thread = threading.Thread(target=scheduler_agent, args=(config, shared_data), daemon=True)
            thread.start()
            try:
                time.sleep(0.30)
            finally:
                shared_data["shutdown_event"].set()
                thread.join(timeout=3)

        self.assertEqual(_CountingModbusClient.write_counts.get(("127.0.0.1", 5020, p_reg), 0), 0)
        self.assertEqual(_CountingModbusClient.write_counts.get(("127.0.0.1", 5020, q_reg), 0), 0)
        self.assertEqual(_CountingModbusClient.write_counts.get(("127.0.0.1", 5020, trigger_reg), 0), 0)
        sleep_mock.assert_not_called()

    @patch("modbus.setpoint_io._sleep")
    def test_scheduler_retries_after_trigger_failure_even_when_registers_match(self, sleep_mock):
        _Registry.clear()
        _TriggerFlakyOnceModbusClient.reset()
        config = load_config("config.yaml")
        config["SCHEDULER_PERIOD_S"] = 0.1
        config["ISTENTORE_SCHEDULE_PERIOD_MINUTES"] = 15
        config["PLANTS"]["lib"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["lib"]["modbus"]["local"]["port"] = 5020
        config["PLANTS"]["vrfb"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["vrfb"]["modbus"]["local"]["port"] = 5021

        lib_endpoint = config["PLANTS"]["lib"]["modbus"]["local"]
        lib_points = lib_endpoint["points"]
        p_reg = int(lib_points["p_setpoint"]["address"])
        q_reg = int(lib_points["q_setpoint"]["address"])
        trigger_reg = 95
        lib_points["trigger"] = {
            "name": "trigger",
            "address": trigger_reg,
            "format": "uint16",
            "word_count": 1,
            "byte_count": 2,
            "access": "w",
            "unit": "raw",
            "eng_per_count": 1.0,
        }
        _TriggerFlakyOnceModbusClient.failed_once_addresses = {trigger_reg}

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

        with patch("scheduling.agent.ModbusClient", _TriggerFlakyOnceModbusClient):
            thread = threading.Thread(target=scheduler_agent, args=(config, shared_data), daemon=True)
            thread.start()
            try:
                time.sleep(0.55)
            finally:
                shared_data["shutdown_event"].set()
                thread.join(timeout=3)

        self.assertAlmostEqual(_read_kw(lib_bank, p_reg), 42.0, places=1)
        self.assertAlmostEqual(_read_kw(lib_bank, q_reg), 5.0, places=1)
        self.assertEqual(lib_bank.get_holding_registers(trigger_reg, 1)[0], 0)
        self.assertGreaterEqual(_TriggerFlakyOnceModbusClient.write_counts.get(("127.0.0.1", 5020, p_reg), 0), 2)
        self.assertGreaterEqual(_TriggerFlakyOnceModbusClient.write_counts.get(("127.0.0.1", 5020, q_reg), 0), 2)
        self.assertGreaterEqual(_TriggerFlakyOnceModbusClient.write_counts.get(("127.0.0.1", 5020, trigger_reg), 0), 3)
        dispatch_state = dict(shared_data["dispatch_write_status_by_plant"]["lib"])
        self.assertEqual(dispatch_state["last_attempt_status"], "ok")

    def test_scheduler_skips_write_when_plant_readback_already_matches_target(self):
        _Registry.clear()
        _CountingModbusClient.reset()
        config = load_config("config.yaml")
        config["SCHEDULER_PERIOD_S"] = 0.1
        config["ISTENTORE_SCHEDULE_PERIOD_MINUTES"] = 15
        config["PLANTS"]["lib"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["lib"]["modbus"]["local"]["port"] = 5020
        config["PLANTS"]["vrfb"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["vrfb"]["modbus"]["local"]["port"] = 5021

        lib_endpoint = config["PLANTS"]["lib"]["modbus"]["local"]
        lib_points = lib_endpoint["points"]
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
        _seed_setpoints(lib_bank, lib_endpoint, 42.0, 5.0)
        _seed_q_control_mode_if_configured(lib_bank, lib_endpoint, 1)

        shared_data = _shared_data()
        with shared_data["lock"]:
            shared_data["api_schedule_df_by_plant"]["lib"] = api_df

        with patch("scheduling.agent.ModbusClient", _CountingModbusClient):
            thread = threading.Thread(target=scheduler_agent, args=(config, shared_data), daemon=True)
            thread.start()
            try:
                time.sleep(0.35)
            finally:
                shared_data["shutdown_event"].set()
                thread.join(timeout=3)

        self.assertEqual(_CountingModbusClient.write_counts.get(("127.0.0.1", 5020, p_reg), 0), 0)
        self.assertEqual(_CountingModbusClient.write_counts.get(("127.0.0.1", 5020, q_reg), 0), 0)
        dispatch_state = dict(shared_data["dispatch_write_status_by_plant"]["lib"])
        self.assertTrue(dispatch_state["sending_enabled"])
        self.assertIsNone(dispatch_state.get("last_attempt_source"))

    def test_scheduler_rewrites_when_plant_readback_drifted_but_target_unchanged(self):
        _Registry.clear()
        _CountingModbusClient.reset()
        config = load_config("config.yaml")
        config["SCHEDULER_PERIOD_S"] = 0.1
        config["ISTENTORE_SCHEDULE_PERIOD_MINUTES"] = 15
        config["PLANTS"]["lib"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["lib"]["modbus"]["local"]["port"] = 5020
        config["PLANTS"]["vrfb"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["vrfb"]["modbus"]["local"]["port"] = 5021

        lib_endpoint = config["PLANTS"]["lib"]["modbus"]["local"]
        lib_points = lib_endpoint["points"]
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

        with patch("scheduling.agent.ModbusClient", _CountingModbusClient):
            thread = threading.Thread(target=scheduler_agent, args=(config, shared_data), daemon=True)
            thread.start()
            try:
                time.sleep(0.25)
                _seed_setpoints(lib_bank, lib_endpoint, 7.0, -3.0)
                time.sleep(0.30)
            finally:
                shared_data["shutdown_event"].set()
                thread.join(timeout=3)

        self.assertAlmostEqual(_read_kw(lib_bank, p_reg), 42.0, places=1)
        self.assertAlmostEqual(_read_kw(lib_bank, q_reg), 5.0, places=1)
        self.assertGreaterEqual(_CountingModbusClient.write_counts.get(("127.0.0.1", 5020, p_reg), 0), 2)
        self.assertGreaterEqual(_CountingModbusClient.write_counts.get(("127.0.0.1", 5020, q_reg), 0), 2)

    def test_scheduler_readback_failure_falls_back_to_cache_dedupe(self):
        _Registry.clear()
        _ReadbackFailingModbusClient.reset()
        config = load_config("config.yaml")
        config["SCHEDULER_PERIOD_S"] = 0.1
        config["ISTENTORE_SCHEDULE_PERIOD_MINUTES"] = 15
        config["PLANTS"]["lib"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["lib"]["modbus"]["local"]["port"] = 5020
        config["PLANTS"]["vrfb"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["vrfb"]["modbus"]["local"]["port"] = 5021

        lib_endpoint = config["PLANTS"]["lib"]["modbus"]["local"]
        lib_points = lib_endpoint["points"]
        p_reg = int(lib_points["p_setpoint"]["address"])
        q_reg = int(lib_points["q_setpoint"]["address"])
        _ReadbackFailingModbusClient.failed_read_addresses = {p_reg, q_reg}

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

        with patch("scheduling.agent.ModbusClient", _ReadbackFailingModbusClient):
            thread = threading.Thread(target=scheduler_agent, args=(config, shared_data), daemon=True)
            thread.start()
            try:
                time.sleep(0.45)
            finally:
                shared_data["shutdown_event"].set()
                thread.join(timeout=3)

        self.assertAlmostEqual(_read_kw(lib_bank, p_reg), 42.0, places=1)
        self.assertAlmostEqual(_read_kw(lib_bank, q_reg), 5.0, places=1)
        self.assertEqual(_ReadbackFailingModbusClient.write_counts.get(("127.0.0.1", 5020, p_reg), 0), 1)
        self.assertEqual(_ReadbackFailingModbusClient.write_counts.get(("127.0.0.1", 5020, q_reg), 0), 1)
        dispatch_state = dict(shared_data["dispatch_write_status_by_plant"]["lib"])
        scheduler_ctx = dict(dispatch_state.get("last_scheduler_context") or {})
        self.assertEqual(scheduler_ctx.get("p_compare_source"), "cache_fallback")
        self.assertEqual(scheduler_ctx.get("q_compare_source"), "cache_fallback")
        self.assertFalse(scheduler_ctx.get("p_readback_ok"))
        self.assertFalse(scheduler_ctx.get("q_readback_ok"))
        self.assertIsNone(scheduler_ctx.get("p_readback_mismatch"))
        self.assertIsNone(scheduler_ctx.get("q_readback_mismatch"))

    def test_scheduler_skips_write_when_all_phase_registers_match_target(self):
        _Registry.clear()
        _CountingModbusClient.reset()
        config = load_config("config.yaml")
        config["SCHEDULER_PERIOD_S"] = 0.1
        config["ISTENTORE_SCHEDULE_PERIOD_MINUTES"] = 15
        config["PLANTS"]["lib"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["lib"]["modbus"]["local"]["port"] = 5020
        config["PLANTS"]["vrfb"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["vrfb"]["modbus"]["local"]["port"] = 5021

        lib_endpoint = config["PLANTS"]["lib"]["modbus"]["local"]
        lib_points = lib_endpoint["points"]
        lib_points.pop("p_setpoint", None)
        lib_points.pop("q_setpoint", None)
        lib_points["p_u_setpoint"] = {"name": "p_u_setpoint", "address": 86, "format": "int16", "word_count": 1, "byte_count": 2, "access": "rw", "unit": "kw", "eng_per_count": 0.1}
        lib_points["p_v_setpoint"] = {"name": "p_v_setpoint", "address": 87, "format": "int16", "word_count": 1, "byte_count": 2, "access": "rw", "unit": "kw", "eng_per_count": 0.1}
        lib_points["p_w_setpoint"] = {"name": "p_w_setpoint", "address": 88, "format": "int16", "word_count": 1, "byte_count": 2, "access": "rw", "unit": "kw", "eng_per_count": 0.1}
        lib_points["q_u_setpoint"] = {"name": "q_u_setpoint", "address": 89, "format": "int16", "word_count": 1, "byte_count": 2, "access": "rw", "unit": "kvar", "eng_per_count": 0.1}
        lib_points["q_v_setpoint"] = {"name": "q_v_setpoint", "address": 90, "format": "int16", "word_count": 1, "byte_count": 2, "access": "rw", "unit": "kvar", "eng_per_count": 0.1}
        lib_points["q_w_setpoint"] = {"name": "q_w_setpoint", "address": 91, "format": "int16", "word_count": 1, "byte_count": 2, "access": "rw", "unit": "kvar", "eng_per_count": 0.1}

        lib_bank = _FakeDataBank()
        vrfb_bank = _FakeDataBank()
        _Registry.register("127.0.0.1", 5020, lib_bank)
        _Registry.register("127.0.0.1", 5021, vrfb_bank)

        for point_name in ("p_u_setpoint", "p_v_setpoint", "p_w_setpoint"):
            lib_bank.set_holding_registers(
                lib_points[point_name]["address"],
                encode_point_internal_words(lib_endpoint, point_name, 14.0),
            )
        for point_name in ("q_u_setpoint", "q_v_setpoint", "q_w_setpoint"):
            lib_bank.set_holding_registers(
                lib_points[point_name]["address"],
                encode_point_internal_words(lib_endpoint, point_name, 2.0),
            )
        _seed_q_control_mode_if_configured(lib_bank, lib_endpoint, 1)

        now = now_tz(config)
        api_df = pd.DataFrame(
            {"power_setpoint_kw": [42.0], "reactive_power_setpoint_kvar": [6.0]},
            index=pd.DatetimeIndex([now - pd.Timedelta(minutes=1)]),
        )
        shared_data = _shared_data()
        with shared_data["lock"]:
            shared_data["api_schedule_df_by_plant"]["lib"] = api_df

        with patch("scheduling.agent.ModbusClient", _CountingModbusClient):
            thread = threading.Thread(target=scheduler_agent, args=(config, shared_data), daemon=True)
            thread.start()
            try:
                time.sleep(0.30)
            finally:
                shared_data["shutdown_event"].set()
                thread.join(timeout=3)

        for address in (86, 87, 88, 89, 90, 91):
            self.assertEqual(_CountingModbusClient.write_counts.get(("127.0.0.1", 5020, address), 0), 0)

    def test_scheduler_rewrites_all_phase_registers_when_one_phase_drifted(self):
        _Registry.clear()
        _CountingModbusClient.reset()
        config = load_config("config.yaml")
        config["SCHEDULER_PERIOD_S"] = 0.1
        config["ISTENTORE_SCHEDULE_PERIOD_MINUTES"] = 15
        config["PLANTS"]["lib"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["lib"]["modbus"]["local"]["port"] = 5020
        config["PLANTS"]["vrfb"]["modbus"]["local"]["host"] = "127.0.0.1"
        config["PLANTS"]["vrfb"]["modbus"]["local"]["port"] = 5021

        lib_endpoint = config["PLANTS"]["lib"]["modbus"]["local"]
        lib_points = lib_endpoint["points"]
        lib_points.pop("p_setpoint", None)
        lib_points.pop("q_setpoint", None)
        lib_points["p_u_setpoint"] = {"name": "p_u_setpoint", "address": 86, "format": "int16", "word_count": 1, "byte_count": 2, "access": "rw", "unit": "kw", "eng_per_count": 0.1}
        lib_points["p_v_setpoint"] = {"name": "p_v_setpoint", "address": 87, "format": "int16", "word_count": 1, "byte_count": 2, "access": "rw", "unit": "kw", "eng_per_count": 0.1}
        lib_points["p_w_setpoint"] = {"name": "p_w_setpoint", "address": 88, "format": "int16", "word_count": 1, "byte_count": 2, "access": "rw", "unit": "kw", "eng_per_count": 0.1}
        lib_points["q_u_setpoint"] = {"name": "q_u_setpoint", "address": 89, "format": "int16", "word_count": 1, "byte_count": 2, "access": "rw", "unit": "kvar", "eng_per_count": 0.1}
        lib_points["q_v_setpoint"] = {"name": "q_v_setpoint", "address": 90, "format": "int16", "word_count": 1, "byte_count": 2, "access": "rw", "unit": "kvar", "eng_per_count": 0.1}
        lib_points["q_w_setpoint"] = {"name": "q_w_setpoint", "address": 91, "format": "int16", "word_count": 1, "byte_count": 2, "access": "rw", "unit": "kvar", "eng_per_count": 0.1}

        lib_bank = _FakeDataBank()
        vrfb_bank = _FakeDataBank()
        _Registry.register("127.0.0.1", 5020, lib_bank)
        _Registry.register("127.0.0.1", 5021, vrfb_bank)

        for point_name in ("p_u_setpoint", "p_v_setpoint", "p_w_setpoint"):
            lib_bank.set_holding_registers(
                lib_points[point_name]["address"],
                encode_point_internal_words(lib_endpoint, point_name, 14.0),
            )
        for point_name in ("q_u_setpoint", "q_v_setpoint", "q_w_setpoint"):
            lib_bank.set_holding_registers(
                lib_points[point_name]["address"],
                encode_point_internal_words(lib_endpoint, point_name, 2.0),
            )
        lib_bank.set_holding_registers(
            lib_points["p_v_setpoint"]["address"],
            encode_point_internal_words(lib_endpoint, "p_v_setpoint", 9.0),
        )

        now = now_tz(config)
        api_df = pd.DataFrame(
            {"power_setpoint_kw": [42.0], "reactive_power_setpoint_kvar": [6.0]},
            index=pd.DatetimeIndex([now - pd.Timedelta(minutes=1)]),
        )
        shared_data = _shared_data()
        with shared_data["lock"]:
            shared_data["api_schedule_df_by_plant"]["lib"] = api_df

        with patch("scheduling.agent.ModbusClient", _CountingModbusClient):
            thread = threading.Thread(target=scheduler_agent, args=(config, shared_data), daemon=True)
            thread.start()
            try:
                time.sleep(0.35)
            finally:
                shared_data["shutdown_event"].set()
                thread.join(timeout=3)

        for address in (86, 87, 88):
            self.assertGreaterEqual(_CountingModbusClient.write_counts.get(("127.0.0.1", 5020, address), 0), 1)
        self.assertAlmostEqual(_read_point_internal_from_bank(lib_bank, lib_endpoint, "p_v_setpoint"), 14.0, places=6)


if __name__ == "__main__":
    unittest.main()
