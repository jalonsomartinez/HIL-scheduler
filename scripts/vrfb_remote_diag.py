#!/usr/bin/env python3
"""VRFB remote diagnostics probe for dashboard/app access-pattern parity analysis."""

from __future__ import annotations

import argparse
import csv
import errno
import socket
import sys
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

# Allow running this script from repository root without installing as a package.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config_loader import load_config
from modbus.codec import decode_engineering_value, encode_engineering_value
from modbus.units import external_to_internal, internal_to_external
from runtime.contracts import resolve_modbus_endpoint

try:
    from pymodbus.client import ModbusTcpClient
    from pymodbus.exceptions import ConnectionException, ModbusException
except Exception as exc:  # pragma: no cover - defensive startup guard
    raise RuntimeError(
        "pymodbus is required for scripts/vrfb_remote_diag.py. "
        "Install dependencies from requirements.txt (expects pymodbus==3.9.2)."
    ) from exc


CSV_COLUMNS = [
    "ts_iso",
    "mode",
    "client_id",
    "op",
    "address",
    "count_or_value",
    "ok",
    "latency_ms",
    "error_type",
    "error_text",
]


DASHBOARD_REGS = [
    {"name": "P_SP", "addr": 1, "typ": "INT16", "scale": 10.0},
    {"name": "Q_SP", "addr": 2, "typ": "INT16", "scale": 10.0},
    {"name": "P_SP_feedback", "addr": 3, "typ": "INT16", "scale": 10.0},
    {"name": "Q_SP_feedback", "addr": 4, "typ": "INT16", "scale": 10.0},
    {"name": "Mode_set", "addr": 5, "typ": "UINT16", "scale": 1.0},
    {"name": "Mode_feedback", "addr": 6, "typ": "UINT16", "scale": 1.0},
    {"name": "Enable_modbus_ctrl", "addr": 7, "typ": "UINT16", "scale": 1.0},
    {"name": "P_out", "addr": 14, "typ": "INT16", "scale": 10.0},
    {"name": "Q_out", "addr": 15, "typ": "INT16", "scale": 10.0},
    {"name": "SOC", "addr": 22, "typ": "UINT16", "scale": 100.0},
    {"name": "V_out_rms", "addr": 29, "typ": "UINT16", "scale": 1000.0},
]


@dataclass
class EndpointContext:
    host: str
    port: int
    slave: int
    timeout_s: float
    endpoint_cfg: dict
    mode: str


class CsvRecorder:
    """Thread-safe CSV recorder for per-operation diagnostics rows."""

    def __init__(self, out_path: Path):
        self.out_path = out_path
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = out_path.open("w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._handle, fieldnames=CSV_COLUMNS)
        self._writer.writeheader()
        self._rows: list[dict] = []
        self._lock = threading.Lock()

    def record(
        self,
        *,
        mode: str,
        client_id: str,
        op: str,
        address: int,
        count_or_value: str,
        ok: bool,
        latency_ms: float,
        error_type: str = "",
        error_text: str = "",
    ) -> None:
        row = {
            "ts_iso": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
            "client_id": client_id,
            "op": op,
            "address": int(address),
            "count_or_value": str(count_or_value),
            "ok": int(bool(ok)),
            "latency_ms": f"{float(latency_ms):.3f}",
            "error_type": str(error_type or ""),
            "error_text": str(error_text or ""),
        }
        with self._lock:
            self._rows.append(row)
            self._writer.writerow(row)
            self._handle.flush()

    def rows_snapshot(self) -> list[dict]:
        with self._lock:
            return [dict(row) for row in self._rows]

    def close(self) -> None:
        try:
            self._handle.close()
        except Exception:
            pass


def classify_exception(exc: Exception) -> tuple[str, str]:
    text = str(exc)
    lower = text.lower()

    if isinstance(exc, ConnectionRefusedError):
        return "connect_refused", text
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "connect_timeout", text
    if isinstance(exc, ConnectionResetError):
        return "connection_reset", text

    if isinstance(exc, OSError):
        code = getattr(exc, "errno", None)
        if code == errno.ECONNREFUSED:
            return "connect_refused", text
        if code in {errno.ETIMEDOUT, errno.EHOSTUNREACH, errno.ENETUNREACH}:
            return "connect_timeout", text
        if code == errno.ECONNRESET:
            return "connection_reset", text

    if isinstance(exc, ConnectionException):
        if "refused" in lower:
            return "connect_refused", text
        if "reset" in lower:
            return "connection_reset", text
        if "timed out" in lower or "timeout" in lower:
            return "connect_timeout", text
        return "connect_timeout", text

    if isinstance(exc, (ValueError, TypeError, OverflowError)):
        return "decode_error", text

    if isinstance(exc, ModbusException):
        return "modbus_exception", text

    if "connection reset" in lower:
        return "connection_reset", text
    if "connection refused" in lower or "actively refused" in lower:
        return "connect_refused", text
    if "timed out" in lower or "timeout" in lower:
        return "connect_timeout", text

    return "unknown_error", text


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    if pct <= 0:
        return float(min(values))
    if pct >= 100:
        return float(max(values))
    ordered = sorted(float(v) for v in values)
    idx = (len(ordered) - 1) * (pct / 100.0)
    low = int(idx)
    high = min(low + 1, len(ordered) - 1)
    weight = idx - low
    return float((1.0 - weight) * ordered[low] + weight * ordered[high])


def timed_connect(
    client: ModbusTcpClient,
    *,
    ctx: EndpointContext,
    recorder: CsvRecorder,
    client_id: str,
    op: str,
) -> bool:
    started = time.perf_counter()
    try:
        ok = bool(client.connect())
    except Exception as exc:
        err_type, err_text = classify_exception(exc)
        recorder.record(
            mode=ctx.mode,
            client_id=client_id,
            op=op,
            address=0,
            count_or_value="-",
            ok=False,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            error_type=err_type,
            error_text=err_text,
        )
        return False

    if not ok:
        recorder.record(
            mode=ctx.mode,
            client_id=client_id,
            op=op,
            address=0,
            count_or_value="-",
            ok=False,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            error_type="connect_timeout",
            error_text="connect() returned False",
        )
        return False

    recorder.record(
        mode=ctx.mode,
        client_id=client_id,
        op=op,
        address=0,
        count_or_value="-",
        ok=True,
        latency_ms=(time.perf_counter() - started) * 1000.0,
    )
    return True


def timed_read_registers(
    client: ModbusTcpClient,
    *,
    ctx: EndpointContext,
    recorder: CsvRecorder,
    client_id: str,
    op: str,
    address: int,
    count: int,
) -> list[int] | None:
    started = time.perf_counter()
    try:
        response = client.read_holding_registers(address=address, count=count, slave=ctx.slave)
    except Exception as exc:
        err_type, err_text = classify_exception(exc)
        recorder.record(
            mode=ctx.mode,
            client_id=client_id,
            op=op,
            address=address,
            count_or_value=str(count),
            ok=False,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            error_type=err_type,
            error_text=err_text,
        )
        return None

    latency_ms = (time.perf_counter() - started) * 1000.0
    if response is None:
        recorder.record(
            mode=ctx.mode,
            client_id=client_id,
            op=op,
            address=address,
            count_or_value=str(count),
            ok=False,
            latency_ms=latency_ms,
            error_type="modbus_exception",
            error_text="null_response",
        )
        return None

    try:
        if response.isError():
            recorder.record(
                mode=ctx.mode,
                client_id=client_id,
                op=op,
                address=address,
                count_or_value=str(count),
                ok=False,
                latency_ms=latency_ms,
                error_type="modbus_exception",
                error_text=str(response),
            )
            return None
    except Exception as exc:
        err_type, err_text = classify_exception(exc)
        recorder.record(
            mode=ctx.mode,
            client_id=client_id,
            op=op,
            address=address,
            count_or_value=str(count),
            ok=False,
            latency_ms=latency_ms,
            error_type=err_type,
            error_text=err_text,
        )
        return None

    registers = getattr(response, "registers", None)
    if registers is None or len(registers) != int(count):
        recorder.record(
            mode=ctx.mode,
            client_id=client_id,
            op=op,
            address=address,
            count_or_value=str(count),
            ok=False,
            latency_ms=latency_ms,
            error_type="decode_error",
            error_text=f"register_count_mismatch expected={count} got={0 if registers is None else len(registers)}",
        )
        return None

    recorder.record(
        mode=ctx.mode,
        client_id=client_id,
        op=op,
        address=address,
        count_or_value=str(count),
        ok=True,
        latency_ms=latency_ms,
    )
    return [int(value) & 0xFFFF for value in registers]


def timed_write_register(
    client: ModbusTcpClient,
    *,
    ctx: EndpointContext,
    recorder: CsvRecorder,
    client_id: str,
    op: str,
    address: int,
    value: int,
) -> bool:
    started = time.perf_counter()
    try:
        response = client.write_register(address=address, value=int(value) & 0xFFFF, slave=ctx.slave)
    except Exception as exc:
        err_type, err_text = classify_exception(exc)
        recorder.record(
            mode=ctx.mode,
            client_id=client_id,
            op=op,
            address=address,
            count_or_value=str(value),
            ok=False,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            error_type=err_type,
            error_text=err_text,
        )
        return False

    latency_ms = (time.perf_counter() - started) * 1000.0
    if response is None:
        recorder.record(
            mode=ctx.mode,
            client_id=client_id,
            op=op,
            address=address,
            count_or_value=str(value),
            ok=False,
            latency_ms=latency_ms,
            error_type="modbus_exception",
            error_text="null_response",
        )
        return False

    try:
        is_error = bool(response.isError())
    except Exception as exc:
        err_type, err_text = classify_exception(exc)
        recorder.record(
            mode=ctx.mode,
            client_id=client_id,
            op=op,
            address=address,
            count_or_value=str(value),
            ok=False,
            latency_ms=latency_ms,
            error_type=err_type,
            error_text=err_text,
        )
        return False

    if is_error:
        recorder.record(
            mode=ctx.mode,
            client_id=client_id,
            op=op,
            address=address,
            count_or_value=str(value),
            ok=False,
            latency_ms=latency_ms,
            error_type="modbus_exception",
            error_text=str(response),
        )
        return False

    recorder.record(
        mode=ctx.mode,
        client_id=client_id,
        op=op,
        address=address,
        count_or_value=str(value),
        ok=True,
        latency_ms=latency_ms,
    )
    return True


def timed_write_registers(
    client: ModbusTcpClient,
    *,
    ctx: EndpointContext,
    recorder: CsvRecorder,
    client_id: str,
    op: str,
    address: int,
    values: list[int],
) -> bool:
    started = time.perf_counter()
    try:
        response = client.write_registers(address=address, values=[int(v) & 0xFFFF for v in values], slave=ctx.slave)
    except Exception as exc:
        err_type, err_text = classify_exception(exc)
        recorder.record(
            mode=ctx.mode,
            client_id=client_id,
            op=op,
            address=address,
            count_or_value=",".join(str(v) for v in values),
            ok=False,
            latency_ms=(time.perf_counter() - started) * 1000.0,
            error_type=err_type,
            error_text=err_text,
        )
        return False

    latency_ms = (time.perf_counter() - started) * 1000.0
    if response is None or response.isError():
        recorder.record(
            mode=ctx.mode,
            client_id=client_id,
            op=op,
            address=address,
            count_or_value=",".join(str(v) for v in values),
            ok=False,
            latency_ms=latency_ms,
            error_type="modbus_exception",
            error_text="null_response" if response is None else str(response),
        )
        return False

    recorder.record(
        mode=ctx.mode,
        client_id=client_id,
        op=op,
        address=address,
        count_or_value=",".join(str(v) for v in values),
        ok=True,
        latency_ms=latency_ms,
    )
    return True


def decode_dashboard_block(regs: list[int]) -> dict[str, float]:
    values: dict[str, float] = {}

    def _int16(raw: int) -> int:
        return raw - 65536 if raw >= 32768 else raw

    for spec in DASHBOARD_REGS:
        idx = int(spec["addr"]) - 1
        raw = int(regs[idx])
        if spec["typ"] == "INT16":
            decoded = _int16(raw)
        else:
            decoded = raw
        values[str(spec["name"])] = float(decoded) / float(spec["scale"])
    return values


def write_point_internal(
    client: ModbusTcpClient,
    *,
    ctx: EndpointContext,
    recorder: CsvRecorder,
    client_id: str,
    point_name: str,
    internal_value: float,
) -> bool:
    point = dict((ctx.endpoint_cfg.get("points") or {}).get(point_name) or {})
    if not point:
        recorder.record(
            mode=ctx.mode,
            client_id=client_id,
            op=f"write.{point_name}",
            address=-1,
            count_or_value=str(internal_value),
            ok=False,
            latency_ms=0.0,
            error_type="decode_error",
            error_text="point_not_configured",
        )
        return False

    try:
        external_value = internal_to_external(point_name, point.get("unit"), internal_value)
        words = [int(v) & 0xFFFF for v in encode_engineering_value(ctx.endpoint_cfg, point, external_value)]
    except Exception as exc:
        err_type, err_text = classify_exception(exc)
        recorder.record(
            mode=ctx.mode,
            client_id=client_id,
            op=f"write.{point_name}",
            address=int(point.get("address", -1)),
            count_or_value=str(internal_value),
            ok=False,
            latency_ms=0.0,
            error_type=err_type,
            error_text=err_text,
        )
        return False

    address = int(point["address"])
    if len(words) == 1:
        return timed_write_register(
            client,
            ctx=ctx,
            recorder=recorder,
            client_id=client_id,
            op=f"write.{point_name}",
            address=address,
            value=int(words[0]),
        )
    return timed_write_registers(
        client,
        ctx=ctx,
        recorder=recorder,
        client_id=client_id,
        op=f"write.{point_name}",
        address=address,
        values=words,
    )


def read_point_internal(
    client: ModbusTcpClient,
    *,
    ctx: EndpointContext,
    recorder: CsvRecorder,
    client_id: str,
    point_name: str,
) -> float | None:
    point = dict((ctx.endpoint_cfg.get("points") or {}).get(point_name) or {})
    if not point:
        return None

    regs = timed_read_registers(
        client,
        ctx=ctx,
        recorder=recorder,
        client_id=client_id,
        op=f"read.{point_name}",
        address=int(point["address"]),
        count=int(point["word_count"]),
    )
    if regs is None:
        return None

    try:
        external_value = decode_engineering_value(ctx.endpoint_cfg, point, regs)
        internal_value = external_to_internal(point_name, point.get("unit"), external_value)
        return float(internal_value)
    except Exception as exc:
        err_type, err_text = classify_exception(exc)
        recorder.record(
            mode=ctx.mode,
            client_id=client_id,
            op=f"decode.{point_name}",
            address=int(point["address"]),
            count_or_value=str(point["word_count"]),
            ok=False,
            latency_ms=0.0,
            error_type=err_type,
            error_text=err_text,
        )
        return None


def run_dashboard_like(
    *,
    ctx: EndpointContext,
    recorder: CsvRecorder,
    duration_s: float,
    poll_s: float,
    dashboard_write_every: int,
    dashboard_p_kw: float,
    dashboard_q_kvar: float,
) -> None:
    client_id = "dashboard_like_client"
    deadline = time.monotonic() + max(1.0, float(duration_s))
    cycle = 0

    client = ModbusTcpClient(host=ctx.host, port=ctx.port, timeout=ctx.timeout_s)
    try:
        while time.monotonic() < deadline:
            cycle += 1
            if not timed_connect(client, ctx=ctx, recorder=recorder, client_id=client_id, op="connect"):
                time.sleep(max(0.01, float(poll_s)))
                continue

            regs = timed_read_registers(
                client,
                ctx=ctx,
                recorder=recorder,
                client_id=client_id,
                op="read.hr_block_1_29",
                address=1,
                count=29,
            )
            if regs is not None:
                try:
                    _ = decode_dashboard_block(regs)
                except Exception as exc:
                    err_type, err_text = classify_exception(exc)
                    recorder.record(
                        mode=ctx.mode,
                        client_id=client_id,
                        op="decode.hr_block_1_29",
                        address=1,
                        count_or_value="29",
                        ok=False,
                        latency_ms=0.0,
                        error_type=err_type,
                        error_text=err_text,
                    )

            should_write = int(dashboard_write_every) > 0 and (cycle % int(dashboard_write_every) == 0)
            if should_write:
                write_point_internal(
                    client,
                    ctx=ctx,
                    recorder=recorder,
                    client_id=client_id,
                    point_name="start_command",
                    internal_value=2,
                )
                write_point_internal(
                    client,
                    ctx=ctx,
                    recorder=recorder,
                    client_id=client_id,
                    point_name="enable",
                    internal_value=1,
                )
                write_point_internal(
                    client,
                    ctx=ctx,
                    recorder=recorder,
                    client_id=client_id,
                    point_name="p_setpoint",
                    internal_value=float(dashboard_p_kw),
                )
                write_point_internal(
                    client,
                    ctx=ctx,
                    recorder=recorder,
                    client_id=client_id,
                    point_name="q_setpoint",
                    internal_value=float(dashboard_q_kvar),
                )
                # Read back command and enable points used by startup/dispatch logic.
                read_point_internal(client, ctx=ctx, recorder=recorder, client_id=client_id, point_name="start_command")
                read_point_internal(client, ctx=ctx, recorder=recorder, client_id=client_id, point_name="enable")
                read_point_internal(client, ctx=ctx, recorder=recorder, client_id=client_id, point_name="p_setpoint")
                read_point_internal(client, ctx=ctx, recorder=recorder, client_id=client_id, point_name="q_setpoint")

            time.sleep(max(0.01, float(poll_s)))
    finally:
        try:
            client.close()
        except Exception:
            pass


def scheduler_lane_step(client: ModbusTcpClient, *, ctx: EndpointContext, recorder: CsvRecorder, client_id: str) -> None:
    if not timed_connect(client, ctx=ctx, recorder=recorder, client_id=client_id, op="connect"):
        return

    point_p = ctx.endpoint_cfg["points"]["p_setpoint"]
    point_q = ctx.endpoint_cfg["points"]["q_setpoint"]

    p_regs = timed_read_registers(
        client,
        ctx=ctx,
        recorder=recorder,
        client_id=client_id,
        op="readback.p_setpoint",
        address=int(point_p["address"]),
        count=int(point_p["word_count"]),
    )
    q_regs = timed_read_registers(
        client,
        ctx=ctx,
        recorder=recorder,
        client_id=client_id,
        op="readback.q_setpoint",
        address=int(point_q["address"]),
        count=int(point_q["word_count"]),
    )

    try:
        p_target = [
            int(v) & 0xFFFF
            for v in encode_engineering_value(
                ctx.endpoint_cfg,
                point_p,
                internal_to_external("p_setpoint", point_p.get("unit"), 0.0),
            )
        ]
        q_target = [
            int(v) & 0xFFFF
            for v in encode_engineering_value(
                ctx.endpoint_cfg,
                point_q,
                internal_to_external("q_setpoint", point_q.get("unit"), 0.0),
            )
        ]
    except Exception as exc:
        err_type, err_text = classify_exception(exc)
        recorder.record(
            mode=ctx.mode,
            client_id=client_id,
            op="encode.targets",
            address=-1,
            count_or_value="p=0.0,q=0.0",
            ok=False,
            latency_ms=0.0,
            error_type=err_type,
            error_text=err_text,
        )
        return

    if p_regs is not None and list(p_regs) != list(p_target):
        write_point_internal(
            client,
            ctx=ctx,
            recorder=recorder,
            client_id=client_id,
            point_name="p_setpoint",
            internal_value=0.0,
        )
    if q_regs is not None and list(q_regs) != list(q_target):
        write_point_internal(
            client,
            ctx=ctx,
            recorder=recorder,
            client_id=client_id,
            point_name="q_setpoint",
            internal_value=0.0,
        )


def measurement_lane_step(client: ModbusTcpClient, *, ctx: EndpointContext, recorder: CsvRecorder, client_id: str) -> None:
    if not timed_connect(client, ctx=ctx, recorder=recorder, client_id=client_id, op="connect"):
        return

    for point_name in (
        "p_setpoint",
        "p_battery",
        "q_setpoint",
        "q_battery",
        "soc",
        "p_poi",
        "q_poi",
        "v_poi",
    ):
        _ = read_point_internal(client, ctx=ctx, recorder=recorder, client_id=client_id, point_name=point_name)


def control_lane_short_session_step(*, ctx: EndpointContext, recorder: CsvRecorder, client_id: str) -> None:
    client = ModbusTcpClient(host=ctx.host, port=ctx.port, timeout=ctx.timeout_s)
    try:
        if not timed_connect(client, ctx=ctx, recorder=recorder, client_id=client_id, op="connect.short_session"):
            return
        for point_name in ("enable", "p_battery", "q_battery", "start_command", "stop_command"):
            _ = read_point_internal(client, ctx=ctx, recorder=recorder, client_id=client_id, point_name=point_name)
    finally:
        try:
            client.close()
        except Exception:
            pass


def run_periodic_worker(
    *,
    period_s: float,
    deadline_monotonic: float,
    stop_event: threading.Event,
    step_fn: Callable[[], None],
) -> None:
    next_tick = time.monotonic()
    while not stop_event.is_set() and time.monotonic() < deadline_monotonic:
        step_fn()
        next_tick += max(0.05, float(period_s))
        sleep_s = next_tick - time.monotonic()
        if sleep_s > 0:
            time.sleep(sleep_s)


def run_app_like_parallel(*, ctx: EndpointContext, recorder: CsvRecorder, duration_s: float) -> None:
    deadline = time.monotonic() + max(1.0, float(duration_s))
    stop_event = threading.Event()

    scheduler_client = ModbusTcpClient(host=ctx.host, port=ctx.port, timeout=ctx.timeout_s)
    measurement_client = ModbusTcpClient(host=ctx.host, port=ctx.port, timeout=ctx.timeout_s)

    scheduler_thread = threading.Thread(
        target=run_periodic_worker,
        kwargs={
            "period_s": 2.0,
            "deadline_monotonic": deadline,
            "stop_event": stop_event,
            "step_fn": lambda: scheduler_lane_step(
                scheduler_client,
                ctx=ctx,
                recorder=recorder,
                client_id="scheduler_lane",
            ),
        },
        daemon=True,
    )
    measurement_thread = threading.Thread(
        target=run_periodic_worker,
        kwargs={
            "period_s": 10.0,
            "deadline_monotonic": deadline,
            "stop_event": stop_event,
            "step_fn": lambda: measurement_lane_step(
                measurement_client,
                ctx=ctx,
                recorder=recorder,
                client_id="measurement_lane",
            ),
        },
        daemon=True,
    )
    control_thread = threading.Thread(
        target=run_periodic_worker,
        kwargs={
            "period_s": 1.0,
            "deadline_monotonic": deadline,
            "stop_event": stop_event,
            "step_fn": lambda: control_lane_short_session_step(
                ctx=ctx,
                recorder=recorder,
                client_id="control_lane",
            ),
        },
        daemon=True,
    )

    scheduler_thread.start()
    measurement_thread.start()
    control_thread.start()

    while time.monotonic() < deadline:
        time.sleep(0.2)

    stop_event.set()
    scheduler_thread.join(timeout=2.0)
    measurement_thread.join(timeout=2.0)
    control_thread.join(timeout=2.0)

    for client in (scheduler_client, measurement_client):
        try:
            client.close()
        except Exception:
            pass


def run_app_like_serial(*, ctx: EndpointContext, recorder: CsvRecorder, duration_s: float) -> None:
    deadline = time.monotonic() + max(1.0, float(duration_s))
    client = ModbusTcpClient(host=ctx.host, port=ctx.port, timeout=ctx.timeout_s)
    lock = threading.Lock()

    next_scheduler = time.monotonic()
    next_measurement = time.monotonic()
    next_control = time.monotonic()

    try:
        while time.monotonic() < deadline:
            now = time.monotonic()

            if now >= next_control:
                with lock:
                    if timed_connect(client, ctx=ctx, recorder=recorder, client_id="serial_shared", op="connect.serial"):
                        for point_name in ("enable", "p_battery", "q_battery", "start_command", "stop_command"):
                            _ = read_point_internal(
                                client,
                                ctx=ctx,
                                recorder=recorder,
                                client_id="control_lane_serial",
                                point_name=point_name,
                            )
                next_control += 1.0

            if now >= next_scheduler:
                with lock:
                    scheduler_lane_step(client, ctx=ctx, recorder=recorder, client_id="scheduler_lane_serial")
                next_scheduler += 2.0

            if now >= next_measurement:
                with lock:
                    measurement_lane_step(client, ctx=ctx, recorder=recorder, client_id="measurement_lane_serial")
                next_measurement += 10.0

            sleep_until = min(next_control, next_scheduler, next_measurement)
            sleep_s = max(0.01, sleep_until - time.monotonic())
            time.sleep(min(0.2, sleep_s))
    finally:
        try:
            client.close()
        except Exception:
            pass


def build_markdown_report(
    *,
    rows: list[dict],
    args: argparse.Namespace,
    out_csv: Path,
    out_md: Path,
) -> None:
    total = len(rows)
    ok_count = sum(1 for row in rows if str(row.get("ok", "0")) in {"1", "True", "true"})
    fail_count = total - ok_count
    status = "PASS" if fail_count == 0 else "FAIL"

    error_counter: Counter[str] = Counter()
    latencies: list[float] = []
    for row in rows:
        try:
            latencies.append(float(row.get("latency_ms", 0.0)))
        except Exception:
            pass
        if str(row.get("ok", "0")) not in {"1", "True", "true"}:
            error_counter[str(row.get("error_type", "") or "unknown_error")] += 1

    p50 = percentile(latencies, 50)
    p90 = percentile(latencies, 90)
    p99 = percentile(latencies, 99)

    recommendation = {
        "dashboard_like": "If this mode fails, prioritize network path / endpoint availability before app changes.",
        "app_like_parallel": "If this mode fails, run app_like_serial next; recovery in serial indicates session/concurrency contention.",
        "app_like_serial": "If this mode passes while app_like_parallel fails, prioritize shared-client/serialized request-stream changes.",
    }.get(str(args.mode), "Run remaining modes and classify via README matrix.")

    lines = [
        "# VRFB Remote Diagnostics Report",
        "",
        "## Run Metadata",
        f"- Timestamp (UTC): {datetime.now(timezone.utc).isoformat()}",
        f"- Mode: {args.mode}",
        f"- Host: {args.host}:{args.port}",
        f"- Slave: {args.slave}",
        f"- Timeout (s): {args.timeout_s}",
        f"- Duration (s): {args.duration_s}",
        f"- Poll (s): {args.poll_s}",
        f"- CSV: `{out_csv}`",
        "",
        "## Pass/Fail Summary",
        f"- Result: {status}",
        f"- Total operations: {total}",
        f"- Successful operations: {ok_count}",
        f"- Failed operations: {fail_count}",
        "",
        "## Error Distribution",
    ]

    if error_counter:
        for error_type, count in sorted(error_counter.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"- {error_type}: {count}")
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "## Latency Percentiles",
            f"- p50 latency (ms): {p50:.2f}",
            f"- p90 latency (ms): {p90:.2f}",
            f"- p99 latency (ms): {p99:.2f}",
            "",
            "## Recommended Next Step",
            f"- {recommendation}",
            "- Combine this report with the other mode reports using the classification rules in README.",
            "",
        ]
    )

    out_md.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="VRFB remote diagnostics probe")
    parser.add_argument("--host", default="10.117.133.26", help="VRFB host")
    parser.add_argument("--port", type=int, default=502, help="Modbus TCP port")
    parser.add_argument("--slave", type=int, default=1, help="Modbus slave/unit id")
    parser.add_argument("--timeout-s", type=float, default=2.0, help="Client timeout in seconds")
    parser.add_argument("--duration-s", type=float, default=180.0, help="Run duration in seconds")
    parser.add_argument(
        "--mode",
        choices=("dashboard_like", "app_like_parallel", "app_like_serial"),
        default="dashboard_like",
        help="Diagnostics mode",
    )
    parser.add_argument("--poll-s", type=float, default=1.0, help="Polling period for dashboard_like mode")
    parser.add_argument("--out", default="", help="Output CSV path")

    # Optional flags to support both read-only and read+write dashboard baselines.
    parser.add_argument(
        "--dashboard-write-every",
        type=int,
        default=0,
        help="Write cadence in cycles for dashboard_like (0 disables writes)",
    )
    parser.add_argument("--dashboard-p-kw", type=float, default=0.0, help="P setpoint used by dashboard_like writes")
    parser.add_argument("--dashboard-q-kvar", type=float, default=0.0, help="Q setpoint used by dashboard_like writes")
    parser.add_argument("--config", default="config.yaml", help="Config file path for point map parity")
    return parser.parse_args()


def resolve_output_path(raw_out: str) -> Path:
    if raw_out:
        return Path(raw_out).expanduser().resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return (REPO_ROOT / "logs" / f"vrfb_remote_diag_{stamp}.csv").resolve()


def main() -> int:
    args = parse_args()
    out_csv = resolve_output_path(args.out)
    out_md = out_csv.with_suffix(".md")

    config = load_config(args.config)
    endpoint = resolve_modbus_endpoint(config, "vrfb", "remote")
    endpoint["host"] = str(args.host)
    endpoint["port"] = int(args.port)

    ctx = EndpointContext(
        host=str(args.host),
        port=int(args.port),
        slave=int(args.slave),
        timeout_s=float(args.timeout_s),
        endpoint_cfg=endpoint,
        mode=str(args.mode),
    )

    recorder = CsvRecorder(out_csv)
    started = time.time()
    try:
        if args.mode == "dashboard_like":
            run_dashboard_like(
                ctx=ctx,
                recorder=recorder,
                duration_s=float(args.duration_s),
                poll_s=float(args.poll_s),
                dashboard_write_every=int(args.dashboard_write_every),
                dashboard_p_kw=float(args.dashboard_p_kw),
                dashboard_q_kvar=float(args.dashboard_q_kvar),
            )
        elif args.mode == "app_like_parallel":
            run_app_like_parallel(
                ctx=ctx,
                recorder=recorder,
                duration_s=float(args.duration_s),
            )
        elif args.mode == "app_like_serial":
            run_app_like_serial(
                ctx=ctx,
                recorder=recorder,
                duration_s=float(args.duration_s),
            )
        else:  # pragma: no cover - argparse choices guard
            raise ValueError(f"Unsupported mode: {args.mode}")
    finally:
        rows = recorder.rows_snapshot()
        recorder.close()

    build_markdown_report(rows=rows, args=args, out_csv=out_csv, out_md=out_md)

    elapsed = time.time() - started
    total = len(rows)
    failures = sum(1 for row in rows if str(row.get("ok", "0")) not in {"1", "True", "true"})
    print(f"[vrfb_remote_diag] done mode={args.mode} elapsed_s={elapsed:.1f} ops={total} failures={failures}")
    print(f"[vrfb_remote_diag] csv={out_csv}")
    print(f"[vrfb_remote_diag] report={out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
