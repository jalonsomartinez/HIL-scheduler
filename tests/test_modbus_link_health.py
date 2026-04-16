import unittest
from datetime import timedelta
from unittest.mock import patch

import modbus.client as modbus_client_module


class _FakeResponse:
    def __init__(self, registers):
        self.registers = list(registers)

    def isError(self):
        return False


class _FakePymodbusTcpClient:
    instances = []

    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.connected = False
        self.connect_calls = 0
        self.close_calls = 0
        self.fail_reads = False
        self.fail_writes = False
        _FakePymodbusTcpClient.instances.append(self)

    def connect(self):
        self.connect_calls += 1
        self.connected = True
        return True

    def close(self):
        self.close_calls += 1
        self.connected = False

    def read_holding_registers(self, address, count, slave):
        if self.fail_reads:
            return None
        return _FakeResponse([0] * int(count))

    def write_register(self, address, value, slave):
        if self.fail_writes:
            return None
        return _FakeResponse([])

    def write_registers(self, address, values, slave):
        if self.fail_writes:
            return None
        return _FakeResponse([])


class ModbusLinkHealthTests(unittest.TestCase):
    def setUp(self):
        _FakePymodbusTcpClient.instances.clear()
        modbus_client_module.ModbusClient._shared_transports = {}

    def test_snapshot_unknown_before_any_transaction(self):
        with patch("modbus.client._PymodbusTcpClient", _FakePymodbusTcpClient):
            modbus_client_module.ModbusClient(host="127.0.0.1", port=502, timeout=2.0, reset_after_consecutive_failures=2)
            health = modbus_client_module.snapshot_modbus_transport_health("127.0.0.1", 502, stale_after_s=15.0)

        self.assertEqual(health["state"], "unknown")
        self.assertIsNone(health["last_success_at"])
        self.assertEqual(health["consecutive_failures"], 0)

    def test_transport_health_degrades_then_recovers_after_forced_reset(self):
        with patch("modbus.client._PymodbusTcpClient", _FakePymodbusTcpClient):
            client = modbus_client_module.ModbusClient(
                host="127.0.0.1",
                port=502,
                timeout=2.0,
                reset_after_consecutive_failures=2,
            )
            self.assertEqual(client.read_holding_registers(1, 1), [0])
            healthy = modbus_client_module.snapshot_modbus_transport_health("127.0.0.1", 502, stale_after_s=15.0)
            self.assertEqual(healthy["state"], "healthy")

            backend = _FakePymodbusTcpClient.instances[0]
            backend.fail_reads = True
            self.assertIsNone(client.read_holding_registers(1, 1))
            degraded = modbus_client_module.snapshot_modbus_transport_health("127.0.0.1", 502, stale_after_s=15.0)
            self.assertEqual(degraded["state"], "degraded")
            self.assertEqual(degraded["consecutive_failures"], 1)

            self.assertIsNone(client.read_holding_registers(1, 1))
            down = modbus_client_module.snapshot_modbus_transport_health("127.0.0.1", 502, stale_after_s=15.0)
            self.assertEqual(down["state"], "down")
            self.assertEqual(down["consecutive_failures"], 2)
            self.assertGreaterEqual(backend.close_calls, 1)

            backend.fail_reads = False
            self.assertEqual(client.read_holding_registers(1, 1), [0])
            recovered = modbus_client_module.snapshot_modbus_transport_health("127.0.0.1", 502, stale_after_s=15.0)
            self.assertEqual(recovered["state"], "healthy")
            self.assertGreaterEqual(recovered["reconnect_count"], 1)

    def test_stale_threshold_triggers_single_reset_until_fresh_success(self):
        with patch("modbus.client._PymodbusTcpClient", _FakePymodbusTcpClient):
            client = modbus_client_module.ModbusClient(
                host="127.0.0.1",
                port=502,
                timeout=2.0,
                reset_after_stale_seconds=5.0,
            )
            self.assertEqual(client.read_holding_registers(1, 1), [0])
            backend = _FakePymodbusTcpClient.instances[0]

            with client._transport.stats_lock:
                client._transport.last_success_at = modbus_client_module._utcnow() - timedelta(seconds=6)

            stale_health = modbus_client_module.snapshot_modbus_transport_health("127.0.0.1", 502, stale_after_s=15.0)
            self.assertEqual(stale_health["last_reset_reason"], "stale_threshold")
            self.assertEqual(stale_health["stale_reset_count"], 1)
            self.assertGreaterEqual(backend.close_calls, 1)

            second_health = modbus_client_module.snapshot_modbus_transport_health("127.0.0.1", 502, stale_after_s=15.0)
            self.assertEqual(second_health["stale_reset_count"], 1)

            self.assertEqual(client.read_holding_registers(1, 1), [0])
            recovered = modbus_client_module.snapshot_modbus_transport_health("127.0.0.1", 502, stale_after_s=15.0)
            self.assertEqual(recovered["state"], "healthy")
            self.assertEqual(recovered["stale_reset_count"], 1)


if __name__ == "__main__":
    unittest.main()
