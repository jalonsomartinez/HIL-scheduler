"""Measurement sampling transport helpers."""

import logging

from modbus.client import ModbusClient

from modbus.grouped_reads import build_read_groups, read_points_internal_grouped
from runtime.contracts import resolve_modbus_endpoint
from time_utils import normalize_timestamp_value

AGGREGATE_SETPOINT_POINT_NAMES = (
    "p_setpoint",
    "q_setpoint",
)
PER_PHASE_P_SETPOINT_POINT_NAMES = (
    "p_u_setpoint",
    "p_v_setpoint",
    "p_w_setpoint",
)
PER_PHASE_Q_SETPOINT_POINT_NAMES = (
    "q_u_setpoint",
    "q_v_setpoint",
    "q_w_setpoint",
)
BASE_MEASUREMENT_POINT_NAMES = (
    "p_battery",
    "q_battery",
    "soc",
    "p_poi",
    "q_poi",
    "v_poi",
)


def get_transport_endpoint(config, plant_id, transport_mode):
    return resolve_modbus_endpoint(config, plant_id, transport_mode)


def resolve_measurement_schema(endpoint):
    points = dict(endpoint.get("points", {}) or {})
    if all(point_name in points for point_name in AGGREGATE_SETPOINT_POINT_NAMES):
        return {
            "setpoint_family": "aggregate",
            "point_names": AGGREGATE_SETPOINT_POINT_NAMES + BASE_MEASUREMENT_POINT_NAMES,
        }
    if all(point_name in points for point_name in PER_PHASE_P_SETPOINT_POINT_NAMES + PER_PHASE_Q_SETPOINT_POINT_NAMES):
        return {
            "setpoint_family": "per_phase",
            "point_names": PER_PHASE_P_SETPOINT_POINT_NAMES + PER_PHASE_Q_SETPOINT_POINT_NAMES + BASE_MEASUREMENT_POINT_NAMES,
        }
    return {
        "setpoint_family": "unknown",
        "point_names": BASE_MEASUREMENT_POINT_NAMES,
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
        state["measurement_schema"] = None
        state["measurement_read_groups"] = None
        state["measurement_schema_log_key"] = None
    if state.get("measurement_schema") is None:
        state["measurement_schema"] = resolve_measurement_schema(endpoint)
    if state.get("measurement_schema_log_key") != (endpoint_key, transport_mode):
        logging.info(
            "Measurement: %s setpoint family=%s for sampling (%s mode)",
            plant_id.upper(),
            state["measurement_schema"]["setpoint_family"],
            transport_mode,
        )
        state["measurement_schema_log_key"] = (endpoint_key, transport_mode)
    if state.get("measurement_read_groups") is None:
        state["measurement_read_groups"] = build_read_groups(
            endpoint,
            state["measurement_schema"]["point_names"],
        )
    endpoint["_measurement_schema"] = state["measurement_schema"]
    endpoint["_measurement_read_groups"] = state["measurement_read_groups"]
    return state.get("client")


def take_measurement(client, endpoint, measurement_timestamp, tz, plant_id):
    if client is None:
        return None

    if not client.is_open:
        if not client.open():
            return None

    try:
        measurement_schema = endpoint.get("_measurement_schema") or resolve_measurement_schema(endpoint)
        setpoint_family = measurement_schema.get("setpoint_family")
        values = read_points_internal_grouped(
            client,
            endpoint,
            measurement_schema.get("point_names", BASE_MEASUREMENT_POINT_NAMES),
            read_groups=endpoint.get("_measurement_read_groups"),
        )
        p_actual_kw = values.get("p_battery")
        q_actual_kvar = values.get("q_battery")
        has_soc_point = "soc" in (endpoint.get("points", {}) or {})
        soc_pu = values.get("soc") if has_soc_point else None
        p_poi_kw = values.get("p_poi")
        q_poi_kvar = values.get("q_poi")
        v_poi_kV = values.get("v_poi")

        if setpoint_family == "aggregate":
            p_setpoint_kw = values.get("p_setpoint")
            q_setpoint_kvar = values.get("q_setpoint")
        elif setpoint_family == "per_phase":
            p_phase_values = [values.get(point_name) for point_name in PER_PHASE_P_SETPOINT_POINT_NAMES]
            q_phase_values = [values.get(point_name) for point_name in PER_PHASE_Q_SETPOINT_POINT_NAMES]
            if any(value is None for value in p_phase_values + q_phase_values):
                return None
            p_setpoint_kw = sum(float(value) for value in p_phase_values)
            q_setpoint_kvar = sum(float(value) for value in q_phase_values)
        else:
            logging.error("Measurement: unsupported setpoint schema for %s", plant_id.upper())
            return None

        if any(
            value is None
            for value in (
                p_setpoint_kw,
                p_actual_kw,
                q_setpoint_kvar,
                q_actual_kvar,
                p_poi_kw,
                q_poi_kvar,
                v_poi_kV,
            )
        ):
            return None
        if has_soc_point and soc_pu is None:
            return None

        return {
            "row": {
                "timestamp": normalize_timestamp_value(measurement_timestamp, tz),
                "p_setpoint_kw": float(p_setpoint_kw),
                "battery_active_power_kw": float(p_actual_kw),
                "q_setpoint_kvar": float(q_setpoint_kvar),
                "battery_reactive_power_kvar": float(q_actual_kvar),
                "soc_pu": None if soc_pu is None else float(soc_pu),
                "p_poi_kw": float(p_poi_kw),
                "q_poi_kvar": float(q_poi_kvar),
                "v_poi_kV": float(v_poi_kV),
            },
            "has_real_soc": bool(has_soc_point),
        }
    except Exception as exc:
        logging.error("Measurement: read error (%s): %s", plant_id.upper(), exc)
        return None
