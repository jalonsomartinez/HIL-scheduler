import unittest
from unittest.mock import MagicMock, patch

from control.modbus_io import wait_until_battery_power_below_threshold


class ControlModbusIoTests(unittest.TestCase):
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
