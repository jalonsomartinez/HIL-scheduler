"""Shared runtime contracts for plant endpoint resolution and naming."""

import re


DEFAULT_MODBUS_REGISTERS = {
    "p_setpoint_in": 86,
    "p_battery": 270,
    "q_setpoint_in": 88,
    "q_battery": 272,
    "enable": 1,
    "soc": 281,
    "p_poi": 290,
    "q_poi": 292,
    "v_poi": 296,
}


def sanitize_plant_name(name, fallback):
    """Normalize plant names for filenames and path-safe IDs."""
    text = str(name).strip().lower()
    text = re.sub(r"[^a-z0-9_-]+", "_", text)
    text = text.strip("_")
    return text or fallback


def resolve_modbus_endpoint(config, plant_id, transport_mode):
    """Resolve a normalized per-plant Modbus endpoint contract."""
    plants_cfg = config.get("PLANTS", {})
    plant_cfg = plants_cfg.get(plant_id, {}) or {}
    endpoint = ((plant_cfg.get("modbus", {}) or {}).get(transport_mode, {})) or {}
    registers = endpoint.get("registers", {}) or {}

    normalized_registers = {
        key: int(registers.get(key, default))
        for key, default in DEFAULT_MODBUS_REGISTERS.items()
    }

    default_port = 5020 if plant_id == "lib" else 5021
    return {
        "mode": transport_mode,
        "host": endpoint.get("host", "localhost"),
        "port": int(endpoint.get("port", default_port)),
        "registers": normalized_registers,
    }
