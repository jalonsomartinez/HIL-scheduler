import unittest
from unittest.mock import MagicMock, patch

from control.modbus_io import send_setpoints, wait_until_battery_power_below_threshold
from modbus.codec import read_point_internal


class _MemoryModbusClient:
    def __init__(self, host, port):
        self.host = str(host)
        self.port = int(port)
        self.is_open = False
        self.registers = {}

    def open(self):
        self.is_open = True
        return True

    def close(self):
        self.is_open = False

    def write_single_register(self, address, value):
        self.registers[int(address)] = int(value)
        return True

    def read_holding_registers(self, address, count):
        return [self.registers.get(int(address) + offset, 0) for offset in range(int(count))]


class ControlModbusIoTests(unittest.TestCase):
    @patch("control.modbus_io.ModbusClient")
    def test_send_setpoints_writes_equal_phase_split_when_endpoint_uses_per_phase_points(self, client_cls):
        client = _MemoryModbusClient("127.0.0.1", 502)
        client_cls.return_value = client
        endpoint_cfg = {
            "host": "127.0.0.1",
            "port": 502,
            "mode": "remote",
            "byte_order": "big",
            "word_order": "msw_first",
            "points": {
                "p_u_setpoint": {"name": "p_u_setpoint", "address": 10, "format": "int16", "word_count": 1, "unit": "kW", "eng_per_count": 0.1},
                "p_v_setpoint": {"name": "p_v_setpoint", "address": 11, "format": "int16", "word_count": 1, "unit": "kW", "eng_per_count": 0.1},
                "p_w_setpoint": {"name": "p_w_setpoint", "address": 12, "format": "int16", "word_count": 1, "unit": "kW", "eng_per_count": 0.1},
                "q_u_setpoint": {"name": "q_u_setpoint", "address": 20, "format": "int16", "word_count": 1, "unit": "kvar", "eng_per_count": 0.1},
                "q_v_setpoint": {"name": "q_v_setpoint", "address": 21, "format": "int16", "word_count": 1, "unit": "kvar", "eng_per_count": 0.1},
                "q_w_setpoint": {"name": "q_w_setpoint", "address": 22, "format": "int16", "word_count": 1, "unit": "kvar", "eng_per_count": 0.1},
            },
        }

        result = send_setpoints(endpoint_cfg, "LIB", 90.0, 30.0)

        self.assertTrue(result)
        self.assertAlmostEqual(read_point_internal(client, endpoint_cfg, "p_u_setpoint"), 30.0, places=6)
        self.assertAlmostEqual(read_point_internal(client, endpoint_cfg, "p_v_setpoint"), 30.0, places=6)
        self.assertAlmostEqual(read_point_internal(client, endpoint_cfg, "p_w_setpoint"), 30.0, places=6)
        self.assertAlmostEqual(read_point_internal(client, endpoint_cfg, "q_u_setpoint"), 10.0, places=6)
        self.assertAlmostEqual(read_point_internal(client, endpoint_cfg, "q_v_setpoint"), 10.0, places=6)
        self.assertAlmostEqual(read_point_internal(client, endpoint_cfg, "q_w_setpoint"), 10.0, places=6)

    @patch("control.modbus_io.time.sleep")
    @patch("control.modbus_io.ModbusClient")
    def test_wait_until_power_threshold_fail_fast_on_connect_failure(self, client_cls, sleep_mock):
        client = MagicMock()
        client.open.return_value = False
        client_cls.return_value = client

        result = wait_until_battery_power_below_threshold(
            {"host": "127.0.0.1", "port": 502, "mode": "remote"},
            threshold_kw=1.0,
            timeout_s=30,
            fail_fast_on_connect_failure=True,
        )

        self.assertFalse(result)
        self.assertEqual(client_cls.call_count, 1)
        sleep_mock.assert_not_called()

    @patch("control.modbus_io.time.sleep")
    @patch("control.modbus_io.read_point_internal")
    @patch("control.modbus_io.ModbusClient")
    def test_wait_until_power_threshold_keeps_reachable_success_behavior(self, client_cls, read_point_mock, sleep_mock):
        client = MagicMock()
        client.open.return_value = True
        client_cls.return_value = client
        read_point_mock.side_effect = [0.5, 0.0]

        result = wait_until_battery_power_below_threshold(
            {"host": "127.0.0.1", "port": 502, "mode": "remote"},
            threshold_kw=1.0,
            timeout_s=30,
            fail_fast_on_connect_failure=True,
        )

        self.assertTrue(result)
        sleep_mock.assert_not_called()
        self.assertEqual(read_point_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
