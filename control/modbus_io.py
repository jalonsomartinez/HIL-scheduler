"""Control-path Modbus I/O helpers for engine control and safe-stop flows."""

import logging
import time

from modbus.client import ModbusClient

from modbus.codec import read_point_internal, write_point_internal
from modbus.setpoint_io import build_setpoint_write_plan, write_setpoint_targets


def write_optional_command_point(endpoint_cfg, plant_label, point_name, value):
    """Write an optional command point; skip when point is not configured."""
    points = dict(endpoint_cfg.get("points", {}) or {})
    point_key = str(point_name or "").strip()
    if not point_key or point_key not in points:
        return {
            "point": point_key,
            "state": "skipped",
            "value": int(value),
            "message": "point_not_configured",
        }

    client = ModbusClient(host=endpoint_cfg["host"], port=endpoint_cfg["port"])
    try:
        if not client.open():
            logging.warning(
                "Control I/O: could not connect to %s (%s mode) for %s write.",
                plant_label,
                endpoint_cfg["mode"],
                point_key,
            )
            return {
                "point": point_key,
                "state": "failed",
                "value": int(value),
                "message": "connect_failed",
            }

        ok = bool(write_point_internal(client, endpoint_cfg, point_key, int(value)))
        return {
            "point": point_key,
            "state": "ok" if ok else "failed",
            "value": int(value),
            "message": None if ok else "write_failed",
        }
    except Exception as exc:
        logging.error("Control I/O: %s write error (%s): %s", point_key, plant_label, exc)
        return {
            "point": point_key,
            "state": "failed",
            "value": int(value),
            "message": str(exc),
        }
    finally:
        try:
            client.close()
        except Exception:
            pass


def set_enable(endpoint_cfg, plant_label, value):
    client = ModbusClient(host=endpoint_cfg["host"], port=endpoint_cfg["port"])
    try:
        if not client.open():
            logging.warning(
                "Control I/O: could not connect to %s (%s mode) for enable.",
                plant_label,
                endpoint_cfg["mode"],
            )
            return False
        return bool(write_point_internal(client, endpoint_cfg, "enable", int(value)))
    except Exception as exc:
        logging.error("Control I/O: enable write error (%s): %s", plant_label, exc)
        return False
    finally:
        try:
            client.close()
        except Exception:
            pass


def send_setpoints(endpoint_cfg, plant_label, p_kw, q_kvar):
    client = ModbusClient(host=endpoint_cfg["host"], port=endpoint_cfg["port"])
    try:
        if not client.open():
            logging.warning(
                "Control I/O: could not connect to %s (%s mode) for setpoints.",
                plant_label,
                endpoint_cfg["mode"],
            )
            return False
        write_plan = build_setpoint_write_plan(endpoint_cfg, p_kw, q_kvar)
        p_result = write_setpoint_targets(client, endpoint_cfg, write_plan["p_targets"])
        q_result = write_setpoint_targets(client, endpoint_cfg, write_plan["q_targets"])
        return bool(p_result["ok"] and q_result["ok"])
    except Exception as exc:
        logging.error("Control I/O: setpoint write error (%s): %s", plant_label, exc)
        return False
    finally:
        try:
            client.close()
        except Exception:
            pass


def read_enable_state(endpoint_cfg):
    client = ModbusClient(host=endpoint_cfg["host"], port=endpoint_cfg["port"])
    try:
        if not client.open():
            return None
        value = read_point_internal(client, endpoint_cfg, "enable")
        if value is None:
            return None
        return int(value)
    except Exception:
        return None
    finally:
        try:
            client.close()
        except Exception:
            pass


def wait_until_battery_power_below_threshold(
    endpoint_cfg,
    threshold_kw=1.0,
    timeout_s=30,
    *,
    fail_fast_on_connect_failure=False,
):
    started_at = time.monotonic()
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        client = ModbusClient(host=endpoint_cfg["host"], port=endpoint_cfg["port"])
        try:
            if not client.open():
                if fail_fast_on_connect_failure:
                    logging.warning(
                        "Control I/O: power decay wait fail-fast on connect failure (%s mode, %s:%s) after %.2fs.",
                        endpoint_cfg.get("mode", "unknown"),
                        endpoint_cfg.get("host"),
                        endpoint_cfg.get("port"),
                        time.monotonic() - started_at,
                    )
                    return False
            else:
                p_kw = read_point_internal(client, endpoint_cfg, "p_battery")
                q_kvar = read_point_internal(client, endpoint_cfg, "q_battery")
                if p_kw is not None and q_kvar is not None:
                    if abs(p_kw) < threshold_kw and abs(q_kvar) < threshold_kw:
                        logging.info(
                            "Control I/O: power decay threshold reached (|P|<%.3f, |Q|<%.3f) in %.2fs.",
                            float(threshold_kw),
                            float(threshold_kw),
                            time.monotonic() - started_at,
                        )
                        return True
        except Exception:
            pass
        finally:
            try:
                client.close()
            except Exception:
                pass
        time.sleep(1.0)
    logging.warning(
        "Control I/O: power decay wait timed out after %.2fs (threshold=%.3f).",
        time.monotonic() - started_at,
        float(threshold_kw),
    )
    return False
