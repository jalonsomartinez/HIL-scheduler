"""Control-path Modbus I/O helpers for engine control and safe-stop flows."""

import logging
import time

from pyModbusTCP.client import ModbusClient

from modbus.codec import read_point_internal, write_point_internal


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
        p_ok = write_point_internal(client, endpoint_cfg, "p_setpoint", p_kw)
        q_ok = write_point_internal(client, endpoint_cfg, "q_setpoint", q_kvar)
        return bool(p_ok and q_ok)
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
