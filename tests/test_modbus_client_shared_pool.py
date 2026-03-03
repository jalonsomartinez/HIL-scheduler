import unittest
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
        _FakePymodbusTcpClient.instances.append(self)

    def connect(self):
        self.connect_calls += 1
        self.connected = True
        return True

    def close(self):
        self.close_calls += 1
        self.connected = False

    def read_holding_registers(self, address, count, slave):
        return _FakeResponse([0] * int(count))

    def write_register(self, address, value, slave):
        return _FakeResponse([])

    def write_registers(self, address, values, slave):
        return _FakeResponse([])


class ModbusClientSharedPoolTests(unittest.TestCase):
    def setUp(self):
        _FakePymodbusTcpClient.instances.clear()
        modbus_client_module.ModbusClient._shared_transports = {}

    def test_clients_share_underlying_transport_per_endpoint(self):
        with patch("modbus.client._PymodbusTcpClient", _FakePymodbusTcpClient):
            c1 = modbus_client_module.ModbusClient(host="127.0.0.1", port=502, unit_id=1, timeout=2.0)
            c2 = modbus_client_module.ModbusClient(host="127.0.0.1", port=502, unit_id=1, timeout=2.0)

            self.assertEqual(len(_FakePymodbusTcpClient.instances), 1)
            backend = _FakePymodbusTcpClient.instances[0]

            self.assertTrue(c1.open())
            self.assertEqual(backend.connect_calls, 1)

            regs = c2.read_holding_registers(1, 1)
            self.assertEqual(regs, [0])
            self.assertEqual(backend.connect_calls, 1)

            c1.close()
            self.assertEqual(backend.close_calls, 0)
            c2.close()
            self.assertEqual(backend.close_calls, 1)


if __name__ == "__main__":
    unittest.main()

