"""Dashboard Modbus I/O helpers for control and safe-stop flows."""

import logging
import time

from pyModbusTCP.client import ModbusClient

from utils import hw_to_kw, int_to_uint16, kw_to_hw, uint16_to_int


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
        return bool(client.write_single_register(endpoint_cfg["enable_reg"], int(value)))
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
        p_ok = client.write_single_register(endpoint_cfg["p_setpoint_reg"], int_to_uint16(kw_to_hw(p_kw)))
        q_ok = client.write_single_register(endpoint_cfg["q_setpoint_reg"], int_to_uint16(kw_to_hw(q_kvar)))
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
        regs = client.read_holding_registers(endpoint_cfg["enable_reg"], 1)
        if not regs:
            return None
        return int(regs[0])
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
                p_regs = client.read_holding_registers(endpoint_cfg["p_battery_reg"], 1)
                q_regs = client.read_holding_registers(endpoint_cfg["q_battery_reg"], 1)
                if p_regs and q_regs:
                    p_kw = hw_to_kw(uint16_to_int(p_regs[0]))
                    q_kvar = hw_to_kw(uint16_to_int(q_regs[0]))
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
