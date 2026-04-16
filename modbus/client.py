"""Modbus client adapter preferring pymodbus and exposing pyModbusTCP-like methods."""

import logging
import threading
from datetime import datetime, timezone

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
        self.stats_lock = threading.Lock()
        self.ref_count = 0
        self.timeout = None
        self.reset_after_consecutive_failures = None
        self.reset_after_stale_seconds = None
        self.last_attempt_at = None
        self.last_success_at = None
        self.last_error = None
        self.consecutive_failures = 0
        self.reconnect_count = 0
        self.last_reset_reason = None
        self.last_reset_at = None
        self.stale_reset_count = 0
        self.stale_reset_armed = True
        self.active_operation = None
        self.active_operation_started_at = None
        self.waiting_count = 0


def _utcnow():
    return datetime.now(timezone.utc)


def _coerce_positive_float(value, default):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    return float(default) if parsed <= 0.0 else parsed


def _coerce_positive_int_or_none(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return None if parsed <= 0 else parsed


class ModbusClient:
    """Compatibility adapter with pyModbusTCP-like API surface."""

    _shared_lock = threading.Lock()
    _shared_transports = {}

    def __init__(
        self,
        host="localhost",
        port=502,
        unit_id=1,
        timeout=30.0,
        reset_after_consecutive_failures=None,
        reset_after_stale_seconds=None,
    ):
        self.host = str(host)
        self.port = int(port)
        self.unit_id = int(unit_id)
        self.timeout = _coerce_positive_float(timeout, 30.0)
        self.reset_after_consecutive_failures = _coerce_positive_int_or_none(reset_after_consecutive_failures)
        self.reset_after_stale_seconds = (
            _coerce_positive_float(reset_after_stale_seconds, 0.0)
            if reset_after_stale_seconds not in (None, 0, 0.0, "0", "0.0")
            else None
        )
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
                    client=self._build_backend_client(backend, timeout=self.timeout),
                    backend=backend,
                )
                transport.timeout = float(self.timeout)
                transport.reset_after_consecutive_failures = self.reset_after_consecutive_failures
                transport.reset_after_stale_seconds = self.reset_after_stale_seconds
                self.__class__._shared_transports[self._transport_key] = transport
            transport.ref_count += 1
        self._transport = transport
        self._configure_shared_transport()

    def _build_backend_client(self, backend, *, timeout=None):
        client_timeout = _coerce_positive_float(timeout, self.timeout)
        if backend == "pymodbus":
            return _PymodbusTcpClient(host=self.host, port=self.port, timeout=client_timeout)
        return _PyModbusTcpClient(
            host=self.host,
            port=self.port,
            unit_id=self.unit_id,
            timeout=client_timeout,
            auto_open=False,
            auto_close=False,
        )

    @property
    def _client(self):
        return self._transport.client

    def _configure_shared_transport(self):
        with self._transport.lock:
            previous_timeout = getattr(self._transport, "timeout", None)
            timeout_changed = previous_timeout is not None and abs(float(previous_timeout) - float(self.timeout)) > 1e-9
            self._transport.timeout = float(self.timeout)
            self._transport.reset_after_consecutive_failures = self.reset_after_consecutive_failures
            self._transport.reset_after_stale_seconds = self.reset_after_stale_seconds
            if timeout_changed:
                self._record_reset_locked("timeout_change")
                self._close_underlying()
                self._transport.client = self._build_backend_client(self._backend, timeout=self.timeout)

    def _is_open_locked(self):
        if self._closed:
            return False
        if self._backend == "pymodbus":
            return bool(getattr(self._client, "connected", False))
        return bool(getattr(self._client, "is_open", False))

    def _start_operation(self, operation_name):
        now_value = _utcnow()
        with self._transport.stats_lock:
            self._transport.active_operation = str(operation_name)
            self._transport.active_operation_started_at = now_value
            self._transport.last_attempt_at = now_value

    def _finish_operation(self, *, success, error_message=None):
        now_value = _utcnow()
        with self._transport.stats_lock:
            self._transport.active_operation = None
            self._transport.active_operation_started_at = None
            if bool(success):
                self._transport.last_success_at = now_value
                self._transport.last_error = None
                self._transport.consecutive_failures = 0
                self._transport.stale_reset_armed = True
                return

            message = str(error_message or "operation_failed")
            self._transport.last_error = {
                "timestamp": now_value,
                "message": message,
            }
            self._transport.consecutive_failures = int(self._transport.consecutive_failures or 0) + 1
            reset_threshold = _coerce_positive_int_or_none(self._transport.reset_after_consecutive_failures)
        if reset_threshold is not None and int(self._transport.consecutive_failures or 0) >= int(reset_threshold):
            with self._transport.lock:
                self._record_reset_locked("error_threshold")
                self._close_underlying()
            with self._transport.stats_lock:
                self._transport.reconnect_count = int(self._transport.reconnect_count or 0) + 1

    def _record_reset_locked(self, reason):
        now_value = _utcnow()
        with self._transport.stats_lock:
            self._transport.last_reset_reason = str(reason)
            self._transport.last_reset_at = now_value
            if str(reason) == "stale_threshold":
                self._transport.stale_reset_count = int(self._transport.stale_reset_count or 0) + 1
                self._transport.stale_reset_armed = False

    def _reset_if_stale_locked(self, *, now_value=None):
        now_value = now_value if now_value is not None else _utcnow()
        with self._transport.stats_lock:
            last_success_at = self._transport.last_success_at
            stale_threshold = self._transport.reset_after_stale_seconds
            active_operation = self._transport.active_operation
            stale_reset_armed = bool(self._transport.stale_reset_armed)
        if last_success_at is None or stale_threshold in (None, 0):
            return False
        if active_operation:
            return False
        if not stale_reset_armed:
            return False
        age_s = max(0.0, (now_value - last_success_at).total_seconds())
        if age_s <= float(stale_threshold):
            return False
        self._record_reset_locked("stale_threshold")
        self._close_underlying()
        with self._transport.stats_lock:
            self._transport.reconnect_count = int(self._transport.reconnect_count or 0) + 1
        return True

    def _ensure_open_locked(self):
        if self._closed:
            return False
        self._reset_if_stale_locked()
        if self._is_open_locked():
            return True
        if self._backend == "pymodbus":
            ok = bool(self._client.connect())
        else:
            ok = bool(self._client.open())
        if ok:
            with self._transport.stats_lock:
                if (
                    self._transport.last_attempt_at is not None
                    or self._transport.last_success_at is not None
                    or self._transport.last_error is not None
                ):
                    self._transport.reconnect_count = int(self._transport.reconnect_count or 0) + 1
        return ok

    def _run_transport_call(self, operation_name, call_fn, *, success_predicate, failure_message):
        if self._closed:
            return None
        waiting_decremented = False
        with self._transport.stats_lock:
            self._transport.waiting_count = int(self._transport.waiting_count or 0) + 1
        try:
            with self._transport.lock:
                with self._transport.stats_lock:
                    self._transport.waiting_count = max(0, int(self._transport.waiting_count or 0) - 1)
                waiting_decremented = True
                self._start_operation(operation_name)
                try:
                    if not self._ensure_open_locked():
                        self._finish_operation(success=False, error_message="connect_failed")
                        return None
                    result = call_fn()
                except Exception as exc:
                    self._finish_operation(success=False, error_message=str(exc))
                    return None
                success = bool(success_predicate(result))
                self._finish_operation(
                    success=success,
                    error_message=None if success else failure_message,
                )
                return result
        finally:
            if not waiting_decremented:
                with self._transport.stats_lock:
                    self._transport.waiting_count = max(0, int(self._transport.waiting_count or 0) - 1)

    @property
    def is_open(self):
        with self._transport.lock:
            return self._is_open_locked()

    def open(self):
        result = self._run_transport_call(
            "open",
            lambda: True if self._ensure_open_locked() else False,
            success_predicate=lambda value: bool(value),
            failure_message="connect_failed",
        )
        if result is None:
            return False
        return bool(result)

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

    def read_holding_registers(self, address, count):
        reg_addr = int(address)
        reg_count = int(count)
        result = self._run_transport_call(
            f"read[{reg_addr}:{reg_count}]",
            lambda: self._read_holding_registers_locked(reg_addr, reg_count),
            success_predicate=lambda value: value is not None and len(value) == reg_count,
            failure_message=f"read_failed:{reg_addr}:{reg_count}",
        )
        if result is None:
            return None
        return result

    def _read_holding_registers_locked(self, reg_addr, reg_count):
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

    def write_single_register(self, address, value):
        reg_addr = int(address)
        reg_value = int(value) & 0xFFFF
        result = self._run_transport_call(
            f"write_single[{reg_addr}]",
            lambda: self._write_single_register_locked(reg_addr, reg_value),
            success_predicate=lambda value: bool(value),
            failure_message=f"write_single_failed:{reg_addr}",
        )
        if result is None:
            return False
        return bool(result)

    def _write_single_register_locked(self, reg_addr, reg_value):
        if self._backend == "pymodbus":
            response = self._client.write_register(address=reg_addr, value=reg_value, slave=self.unit_id)
            return bool(response is not None and not response.isError())
        return bool(self._client.write_single_register(reg_addr, reg_value))

    def write_multiple_registers(self, address, values):
        reg_addr = int(address)
        reg_values = [int(value) & 0xFFFF for value in (values or [])]
        result = self._run_transport_call(
            f"write_multi[{reg_addr}:{len(reg_values)}]",
            lambda: self._write_multiple_registers_locked(reg_addr, reg_values),
            success_predicate=lambda value: bool(value),
            failure_message=f"write_multiple_failed:{reg_addr}:{len(reg_values)}",
        )
        if result is None:
            return False
        return bool(result)

    def _write_multiple_registers_locked(self, reg_addr, reg_values):
        if self._backend == "pymodbus":
            response = self._client.write_registers(address=reg_addr, values=reg_values, slave=self.unit_id)
            return bool(response is not None and not response.isError())
        return bool(self._client.write_multiple_registers(reg_addr, reg_values))


def instantiate_modbus_client(client_cls, endpoint_cfg, *, unit_id=1, default_timeout=30.0):
    endpoint = dict(endpoint_cfg or {})
    kwargs = {
        "host": endpoint.get("host", "localhost"),
        "port": int(endpoint.get("port", 502)),
        "unit_id": int(unit_id),
    }
    timeout_s = endpoint.get("timeout_s")
    if timeout_s is not None:
        kwargs["timeout"] = _coerce_positive_float(timeout_s, default_timeout)
    reset_after = endpoint.get("reset_after_consecutive_failures")
    if reset_after is not None:
        parsed_reset = _coerce_positive_int_or_none(reset_after)
        if parsed_reset is not None:
            kwargs["reset_after_consecutive_failures"] = parsed_reset
    reset_after_stale = endpoint.get("reset_after_stale_seconds")
    if reset_after_stale is not None:
        parsed_stale_reset = _coerce_positive_float(reset_after_stale, 0.0)
        if parsed_stale_reset > 0.0:
            kwargs["reset_after_stale_seconds"] = parsed_stale_reset
    try:
        return client_cls(**kwargs)
    except TypeError:
        kwargs.pop("reset_after_stale_seconds", None)
        kwargs.pop("reset_after_consecutive_failures", None)
        kwargs.pop("timeout", None)
        kwargs.pop("unit_id", None)
        return client_cls(**kwargs)


def snapshot_modbus_transport_health(host, port, unit_id=1, *, stale_after_s=15.0):
    host_text = str(host)
    port_value = int(port)
    unit_value = int(unit_id)
    transport = None
    with ModbusClient._shared_lock:
        for (_, key_host, key_port, key_unit), candidate in ModbusClient._shared_transports.items():
            if str(key_host) == host_text and int(key_port) == port_value and int(key_unit) == unit_value:
                transport = candidate
                break
    if transport is None:
        return {
            "state": "unknown",
            "last_success_at": None,
            "last_attempt_at": None,
            "consecutive_failures": 0,
            "last_error": None,
            "reconnect_count": 0,
            "active_operation": None,
            "active_operation_age_s": None,
            "waiting_count": 0,
            "timeout_s": None,
            "reset_after_consecutive_failures": None,
            "reset_after_stale_seconds": None,
            "last_reset_reason": None,
            "last_reset_at": None,
            "stale_reset_count": 0,
        }

    now_value = _utcnow()
    with transport.lock:
        temp_client = object.__new__(ModbusClient)
        temp_client._transport = transport
        temp_client._backend = getattr(transport, "backend", "pymodbus")
        temp_client._closed = False
        temp_client._reset_if_stale_locked(now_value=now_value)
        with transport.stats_lock:
            active_started = transport.active_operation_started_at
            if active_started is None:
                active_age_s = None
            else:
                active_age_s = max(0.0, (now_value - active_started).total_seconds())
            timeout_s = _coerce_positive_float(getattr(transport, "timeout", None), 30.0)
            reset_threshold = _coerce_positive_int_or_none(getattr(transport, "reset_after_consecutive_failures", None))
            stale_reset_threshold = _coerce_positive_float(getattr(transport, "reset_after_stale_seconds", None), 0.0)
            if stale_reset_threshold <= 0.0:
                stale_reset_threshold = None
            last_success_at = transport.last_success_at
            last_attempt_at = transport.last_attempt_at
            consecutive_failures = int(transport.consecutive_failures or 0)
            last_error = dict(transport.last_error or {}) if isinstance(transport.last_error, dict) else transport.last_error
            reconnect_count = int(transport.reconnect_count or 0)
            waiting_count = int(transport.waiting_count or 0)
            active_operation = transport.active_operation
            last_reset_reason = transport.last_reset_reason
            last_reset_at = transport.last_reset_at
            stale_reset_count = int(transport.stale_reset_count or 0)

    if last_success_at is None and last_attempt_at is None:
        state = "unknown"
    elif last_success_at is None:
        state = "down"
    else:
        age_s = max(0.0, (now_value - last_success_at).total_seconds())
        health_timeout_s = max(float(stale_after_s or 0.0), timeout_s * 3.0, 3.0)
        degraded_after_s = max(timeout_s * 2.0, health_timeout_s * 0.5)
        down_threshold = int(reset_threshold or 3)
        if age_s > health_timeout_s or consecutive_failures >= down_threshold:
            state = "down"
        elif consecutive_failures > 0 or age_s > degraded_after_s:
            state = "degraded"
        else:
            state = "healthy"

    return {
        "state": state,
        "last_success_at": last_success_at,
        "last_attempt_at": last_attempt_at,
        "consecutive_failures": consecutive_failures,
        "last_error": last_error,
        "reconnect_count": reconnect_count,
        "active_operation": active_operation,
        "active_operation_age_s": None if active_age_s is None else round(float(active_age_s), 3),
        "waiting_count": waiting_count,
        "timeout_s": timeout_s,
        "reset_after_consecutive_failures": reset_threshold,
        "reset_after_stale_seconds": stale_reset_threshold,
        "last_reset_reason": last_reset_reason,
        "last_reset_at": last_reset_at,
        "stale_reset_count": stale_reset_count,
    }
