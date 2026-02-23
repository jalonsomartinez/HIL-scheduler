"""Shared runtime contracts for plant endpoint resolution and naming."""

import copy
import re


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
    default_port = 5020 if plant_id == "lib" else 5021
    points = endpoint.get("points", {}) or {}
    return {
        "mode": transport_mode,
        "host": endpoint.get("host", "localhost"),
        "port": int(endpoint.get("port", default_port)),
        "byte_order": endpoint.get("byte_order"),
        "word_order": endpoint.get("word_order"),
        "points": copy.deepcopy(points),
    }
