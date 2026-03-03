"""Modbus client adapter preferring pymodbus and exposing pyModbusTCP-like methods."""

import logging

try:
    from pymodbus.client import ModbusTcpClient as _PymodbusTcpClient
except Exception:  # pragma: no cover - exercised when pymodbus is not installed
    _PymodbusTcpClient = None

from pyModbusTCP.client import ModbusClient as _PyModbusTcpClient


_LOGGER = logging.getLogger(__name__)
_FALLBACK_WARNED = False


class ModbusClient:
    """Compatibility adapter with pyModbusTCP-like API surface."""

    def __init__(self, host="localhost", port=502, unit_id=1, timeout=30.0):
        self.host = str(host)
        self.port = int(port)
        self.unit_id = int(unit_id)
        self.timeout = float(timeout)

        if _PymodbusTcpClient is not None:
            self._backend = "pymodbus"
            self._client = _PymodbusTcpClient(host=self.host, port=self.port, timeout=self.timeout)
        else:
            global _FALLBACK_WARNED
            if not _FALLBACK_WARNED:
                _LOGGER.warning("pymodbus not installed; falling back to pyModbusTCP client backend.")
                _FALLBACK_WARNED = True
            self._backend = "pyModbusTCP"
            self._client = _PyModbusTcpClient(
                host=self.host,
                port=self.port,
                unit_id=self.unit_id,
                timeout=self.timeout,
                auto_open=False,
                auto_close=False,
            )

    @property
    def is_open(self):
        if self._backend == "pymodbus":
            return bool(getattr(self._client, "connected", False))
        return bool(getattr(self._client, "is_open", False))

    def open(self):
        try:
            if self._backend == "pymodbus":
                return bool(self._client.connect())
            return bool(self._client.open())
        except Exception:
            return False

    def close(self):
        try:
            self._client.close()
        except Exception:
            pass

    def _ensure_open(self):
        if self.is_open:
            return True
        return bool(self.open())

    def read_holding_registers(self, address, count):
        reg_addr = int(address)
        reg_count = int(count)
        if not self._ensure_open():
            return None

        if self._backend == "pymodbus":
            try:
                response = self._client.read_holding_registers(address=reg_addr, count=reg_count, slave=self.unit_id)
            except Exception:
                return None
            if response is None:
                return None
            try:
                if response.isError():
                    return None
            except Exception:
                return None
            registers = getattr(response, "registers", None)
            if registers is None:
                return None
            return [int(value) & 0xFFFF for value in registers]

        return self._client.read_holding_registers(reg_addr, reg_count)

    def write_single_register(self, address, value):
        reg_addr = int(address)
        reg_value = int(value) & 0xFFFF
        if not self._ensure_open():
            return False

        if self._backend == "pymodbus":
            try:
                response = self._client.write_register(address=reg_addr, value=reg_value, slave=self.unit_id)
                return bool(response is not None and not response.isError())
            except Exception:
                return False

        return bool(self._client.write_single_register(reg_addr, reg_value))

    def write_multiple_registers(self, address, values):
        reg_addr = int(address)
        reg_values = [int(value) & 0xFFFF for value in (values or [])]
        if not self._ensure_open():
            return False

        if self._backend == "pymodbus":
            try:
                response = self._client.write_registers(address=reg_addr, values=reg_values, slave=self.unit_id)
                return bool(response is not None and not response.isError())
            except Exception:
                return False

        return bool(self._client.write_multiple_registers(reg_addr, reg_values))
