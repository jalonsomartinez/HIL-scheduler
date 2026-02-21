"""Measurement sampling transport helpers."""

import logging

from pyModbusTCP.client import ModbusClient

from runtime_contracts import resolve_modbus_endpoint
from time_utils import normalize_timestamp_value
from utils import hw_to_kw, uint16_to_int


def get_transport_endpoint(config, plant_id, transport_mode):
    endpoint = resolve_modbus_endpoint(config, plant_id, transport_mode)
    registers = endpoint["registers"]
    return {
        "host": endpoint.get("host", "localhost"),
        "port": int(endpoint.get("port", 5020 if plant_id == "lib" else 5021)),
        "p_setpoint_reg": registers["p_setpoint_in"],
        "p_battery_reg": registers["p_battery"],
        "q_setpoint_reg": registers["q_setpoint_in"],
        "q_battery_reg": registers["q_battery"],
        "soc_reg": registers["soc"],
        "p_poi_reg": registers["p_poi"],
        "q_poi_reg": registers["q_poi"],
        "v_poi_reg": registers["v_poi"],
    }


def ensure_client(state, endpoint, plant_id, transport_mode):
    endpoint_key = (endpoint["host"], endpoint["port"])
    if state.get("endpoint_key") != endpoint_key:
        if state.get("client") is not None:
            try:
                state["client"].close()
            except Exception:
                pass

        state["client"] = ModbusClient(host=endpoint["host"], port=endpoint["port"])
        state["endpoint_key"] = endpoint_key
        logging.info(
            "Measurement: %s endpoint -> %s:%s (%s mode)",
            plant_id.upper(),
            endpoint["host"],
            endpoint["port"],
            transport_mode,
        )
    return state.get("client")


def take_measurement(client, endpoint, measurement_timestamp, tz, plant_id):
    if client is None:
        return None

    if not client.is_open:
        if not client.open():
            return None

    try:
        regs_p_setpoint = client.read_holding_registers(endpoint["p_setpoint_reg"], 1)
        regs_p_actual = client.read_holding_registers(endpoint["p_battery_reg"], 1)
        regs_q_setpoint = client.read_holding_registers(endpoint["q_setpoint_reg"], 1)
        regs_q_actual = client.read_holding_registers(endpoint["q_battery_reg"], 1)
        regs_soc = client.read_holding_registers(endpoint["soc_reg"], 1)
        regs_p_poi = client.read_holding_registers(endpoint["p_poi_reg"], 1)
        regs_q_poi = client.read_holding_registers(endpoint["q_poi_reg"], 1)
        regs_v_poi = client.read_holding_registers(endpoint["v_poi_reg"], 1)

        if not all([regs_p_setpoint, regs_p_actual, regs_q_setpoint, regs_q_actual, regs_soc, regs_p_poi, regs_q_poi, regs_v_poi]):
            return None

        return {
            "timestamp": normalize_timestamp_value(measurement_timestamp, tz),
            "p_setpoint_kw": hw_to_kw(uint16_to_int(regs_p_setpoint[0])),
            "battery_active_power_kw": hw_to_kw(uint16_to_int(regs_p_actual[0])),
            "q_setpoint_kvar": hw_to_kw(uint16_to_int(regs_q_setpoint[0])),
            "battery_reactive_power_kvar": hw_to_kw(uint16_to_int(regs_q_actual[0])),
            "soc_pu": regs_soc[0] / 10000.0,
            "p_poi_kw": hw_to_kw(uint16_to_int(regs_p_poi[0])),
            "q_poi_kvar": hw_to_kw(uint16_to_int(regs_q_poi[0])),
            "v_poi_pu": regs_v_poi[0] / 100.0,
        }
    except Exception as exc:
        logging.error("Measurement: read error (%s): %s", plant_id.upper(), exc)
        return None
