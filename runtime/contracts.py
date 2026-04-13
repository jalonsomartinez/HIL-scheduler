"""Shared runtime contracts for plant endpoint resolution, naming, and limits."""

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
    power_limits = dict(((plant_cfg.get("model", {}) or {}).get("power_limits", {}) or {}))
    default_port = 5020 if plant_id == "lib" else 5021
    points = endpoint.get("points", {}) or {}
    return {
        "mode": transport_mode,
        "host": endpoint.get("host", "localhost"),
        "port": int(endpoint.get("port", default_port)),
        "byte_order": endpoint.get("byte_order"),
        "word_order": endpoint.get("word_order"),
        "power_limits": copy.deepcopy(power_limits),
        "points": copy.deepcopy(points),
    }


def resolve_plant_power_limits(config, plant_id):
    """Resolve normalized configured P/Q limits for a plant."""
    plants_cfg = config.get("PLANTS", {})
    plant_cfg = plants_cfg.get(plant_id, {}) or {}
    power_limits = dict(((plant_cfg.get("model", {}) or {}).get("power_limits", {}) or {}))
    return {
        "p_max_kw": float(power_limits.get("p_max_kw", 1000.0)),
        "p_min_kw": float(power_limits.get("p_min_kw", -1000.0)),
        "q_max_kvar": float(power_limits.get("q_max_kvar", 600.0)),
        "q_min_kvar": float(power_limits.get("q_min_kvar", -600.0)),
    }


def clamp_dispatch_setpoints(p_kw, q_kvar, power_limits):
    """Clamp requested dispatch setpoints to configured plant limits."""
    limits = dict(power_limits or {})
    requested_p_kw = float(0.0 if p_kw is None else p_kw)
    requested_q_kvar = float(0.0 if q_kvar is None else q_kvar)

    p_min_kw = float(limits.get("p_min_kw", -1000.0))
    p_max_kw = float(limits.get("p_max_kw", 1000.0))
    q_min_kvar = float(limits.get("q_min_kvar", -600.0))
    q_max_kvar = float(limits.get("q_max_kvar", 600.0))

    applied_p_kw = min(max(requested_p_kw, p_min_kw), p_max_kw)
    applied_q_kvar = min(max(requested_q_kvar, q_min_kvar), q_max_kvar)
    p_clamped = applied_p_kw != requested_p_kw
    q_clamped = applied_q_kvar != requested_q_kvar

    return {
        "requested_p_kw": requested_p_kw,
        "requested_q_kvar": requested_q_kvar,
        "applied_p_kw": applied_p_kw,
        "applied_q_kvar": applied_q_kvar,
        "p_clamped": bool(p_clamped),
        "q_clamped": bool(q_clamped),
        "any_clamped": bool(p_clamped or q_clamped),
        "limits": {
            "p_min_kw": p_min_kw,
            "p_max_kw": p_max_kw,
            "q_min_kvar": q_min_kvar,
            "q_max_kvar": q_max_kvar,
        },
    }
