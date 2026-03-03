"""Modbus client adapter preferring pymodbus and exposing pyModbusTCP-like methods."""

import logging
import threading

try:
    from pymodbus.client import ModbusTcpClient as _PymodbusTcpClient
except Exception:  # pragma: no cover - exercised when pymodbus is not installed
    _PymodbusTcpClient = None

from pyModbusTCP.client import ModbusClient as _PyModbusTcpClient


_LOGGER = logging.getLogger(__name__)
_FALLBACK_WARNED = False


class _SharedTransport:
    """Process-local shared transport to avoid multi-session contention per endpoint."""

    def __init__(self, client, backend):
        self.client = client
        self.backend = backend
        self.lock = threading.RLock()
        self.ref_count = 0


class ModbusClient:
    """Compatibility adapter with pyModbusTCP-like API surface."""

    _shared_lock = threading.Lock()
    _shared_transports = {}

    def __init__(self, host="localhost", port=502, unit_id=1, timeout=30.0):
        self.host = str(host)
        self.port = int(port)
        self.unit_id = int(unit_id)
        self.timeout = float(timeout)
        self._closed = False

        if _PymodbusTcpClient is not None:
            backend = "pymodbus"
        else:
            global _FALLBACK_WARNED
            if not _FALLBACK_WARNED:
                _LOGGER.warning("pymodbus not installed; falling back to pyModbusTCP client backend.")
                _FALLBACK_WARNED = True
            backend = "pyModbusTCP"
        self._backend = backend

        self._transport_key = (self._backend, self.host, self.port, self.unit_id)
        with self.__class__._shared_lock:
            transport = self.__class__._shared_transports.get(self._transport_key)
            if transport is None:
                transport = _SharedTransport(
                    client=self._build_backend_client(backend),
                    backend=backend,
                )
                self.__class__._shared_transports[self._transport_key] = transport
            transport.ref_count += 1
        self._transport = transport

    def _build_backend_client(self, backend):
        if backend == "pymodbus":
            return _PymodbusTcpClient(host=self.host, port=self.port, timeout=self.timeout)
        return _PyModbusTcpClient(
            host=self.host,
            port=self.port,
            unit_id=self.unit_id,
            timeout=self.timeout,
            auto_open=False,
            auto_close=False,
        )

    @property
    def _client(self):
        return self._transport.client

    @property
    def is_open(self):
        if self._closed:
            return False
        with self._transport.lock:
            if self._backend == "pymodbus":
                return bool(getattr(self._client, "connected", False))
            return bool(getattr(self._client, "is_open", False))

    def open(self):
        if self._closed:
            return False
        try:
            with self._transport.lock:
                if self._backend == "pymodbus":
                    return bool(self._client.connect())
                return bool(self._client.open())
        except Exception:
            return False

    def _close_underlying(self):
        try:
            self._client.close()
        except Exception:
            pass

    def close(self):
        if self._closed:
            return
        self._closed = True

        should_close = False
        with self.__class__._shared_lock:
            transport = self.__class__._shared_transports.get(self._transport_key)
            if transport is None:
                return
            transport.ref_count -= 1
            if transport.ref_count <= 0:
                should_close = True
                self.__class__._shared_transports.pop(self._transport_key, None)
        if should_close:
            with self._transport.lock:
                self._close_underlying()

    def _ensure_open(self):
        if self._closed:
            return False
        if self.is_open:
            return True
        return bool(self.open())

    def read_holding_registers(self, address, count):
        reg_addr = int(address)
        reg_count = int(count)
        try:
            with self._transport.lock:
                if not self._ensure_open():
                    return None

                if self._backend == "pymodbus":
                    response = self._client.read_holding_registers(address=reg_addr, count=reg_count, slave=self.unit_id)
                    if response is None:
                        return None
                    if response.isError():
                        return None
                    registers = getattr(response, "registers", None)
                    if registers is None:
                        return None
                    return [int(value) & 0xFFFF for value in registers]
                return self._client.read_holding_registers(reg_addr, reg_count)
        except Exception:
            return None

    def write_single_register(self, address, value):
        reg_addr = int(address)
        reg_value = int(value) & 0xFFFF
        try:
            with self._transport.lock:
                if not self._ensure_open():
                    return False
                if self._backend == "pymodbus":
                    response = self._client.write_register(address=reg_addr, value=reg_value, slave=self.unit_id)
                    return bool(response is not None and not response.isError())
                return bool(self._client.write_single_register(reg_addr, reg_value))
        except Exception:
            return False

    def write_multiple_registers(self, address, values):
        reg_addr = int(address)
        reg_values = [int(value) & 0xFFFF for value in (values or [])]
        try:
            with self._transport.lock:
                if not self._ensure_open():
                    return False
                if self._backend == "pymodbus":
                    response = self._client.write_registers(address=reg_addr, values=reg_values, slave=self.unit_id)
                    return bool(response is not None and not response.isError())
                return bool(self._client.write_multiple_registers(reg_addr, reg_values))
        except Exception:
            return False
