"""Dashboard Modbus I/O helpers for control and safe-stop flows."""

import logging
import time

from pyModbusTCP.client import ModbusClient

from modbus_codec import read_point_internal, write_point_internal


def set_enable(endpoint_cfg, plant_label, value):
    client = ModbusClient(host=endpoint_cfg["host"], port=endpoint_cfg["port"])
    try:
        if not client.open():
            logging.warning(
                "Dashboard: could not connect to %s (%s mode) for enable.",
                plant_label,
                endpoint_cfg["mode"],
            )
            return False
        return bool(write_point_internal(client, endpoint_cfg, "enable", int(value)))
    except Exception as exc:
        logging.error("Dashboard: enable write error (%s): %s", plant_label, exc)
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
                "Dashboard: could not connect to %s (%s mode) for setpoints.",
                plant_label,
                endpoint_cfg["mode"],
            )
            return False
        p_ok = write_point_internal(client, endpoint_cfg, "p_setpoint", p_kw)
        q_ok = write_point_internal(client, endpoint_cfg, "q_setpoint", q_kvar)
        return bool(p_ok and q_ok)
    except Exception as exc:
        logging.error("Dashboard: setpoint write error (%s): %s", plant_label, exc)
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


def wait_until_battery_power_below_threshold(endpoint_cfg, threshold_kw=1.0, timeout_s=30):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        client = ModbusClient(host=endpoint_cfg["host"], port=endpoint_cfg["port"])
        try:
            if client.open():
                p_kw = read_point_internal(client, endpoint_cfg, "p_battery")
                q_kvar = read_point_internal(client, endpoint_cfg, "q_battery")
                if p_kw is not None and q_kvar is not None:
                    if abs(p_kw) < threshold_kw and abs(q_kvar) < threshold_kw:
                        return True
        except Exception:
            pass
        finally:
            try:
                client.close()
            except Exception:
                pass
        time.sleep(1.0)
    return False
