"""Measurement sampling transport helpers."""

import logging

from modbus.client import ModbusClient

from modbus.grouped_reads import build_read_groups, read_points_internal_grouped
from runtime.contracts import resolve_modbus_endpoint
from time_utils import normalize_timestamp_value

MEASUREMENT_POINT_NAMES = (
    "p_setpoint",
    "p_battery",
    "q_setpoint",
    "q_battery",
    "soc",
    "p_poi",
    "q_poi",
    "v_poi",
)


def get_transport_endpoint(config, plant_id, transport_mode):
    return resolve_modbus_endpoint(config, plant_id, transport_mode)


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
    if "_measurement_read_groups" not in endpoint:
        endpoint["_measurement_read_groups"] = build_read_groups(endpoint, MEASUREMENT_POINT_NAMES)
    return state.get("client")


def take_measurement(client, endpoint, measurement_timestamp, tz, plant_id):
    if client is None:
        return None

    if not client.is_open:
        if not client.open():
            return None

    try:
        values = read_points_internal_grouped(
            client,
            endpoint,
            MEASUREMENT_POINT_NAMES,
            read_groups=endpoint.get("_measurement_read_groups"),
        )
        p_setpoint_kw = values.get("p_setpoint")
        p_actual_kw = values.get("p_battery")
        q_setpoint_kvar = values.get("q_setpoint")
        q_actual_kvar = values.get("q_battery")
        soc_pu = values.get("soc")
        p_poi_kw = values.get("p_poi")
        q_poi_kvar = values.get("q_poi")
        v_poi_kV = values.get("v_poi")

        if any(
            value is None
            for value in (
                p_setpoint_kw,
                p_actual_kw,
                q_setpoint_kvar,
                q_actual_kvar,
                soc_pu,
                p_poi_kw,
                q_poi_kvar,
                v_poi_kV,
            )
        ):
            return None

        return {
            "timestamp": normalize_timestamp_value(measurement_timestamp, tz),
            "p_setpoint_kw": float(p_setpoint_kw),
            "battery_active_power_kw": float(p_actual_kw),
            "q_setpoint_kvar": float(q_setpoint_kvar),
            "battery_reactive_power_kvar": float(q_actual_kvar),
            "soc_pu": float(soc_pu),
            "p_poi_kw": float(p_poi_kw),
            "q_poi_kvar": float(q_poi_kvar),
            "v_poi_kV": float(v_poi_kV),
        }
    except Exception as exc:
        logging.error("Measurement: read error (%s): %s", plant_id.upper(), exc)
        return None
