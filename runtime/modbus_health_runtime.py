"""Shared helpers for publishing per-plant Modbus transport health."""

from modbus.client import snapshot_modbus_transport_health


def default_modbus_link_health_entry():
    return {
        "state": "unknown",
        "last_success_at": None,
        "last_attempt_at": None,
        "consecutive_failures": 0,
        "last_error": None,
        "reconnect_count": 0,
        "active_operation": None,
        "active_operation_age_s": None,
        "waiting_count": 0,
        "timeout_s": None,
        "reset_after_consecutive_failures": None,
        "reset_after_stale_seconds": None,
        "last_reset_reason": None,
        "last_reset_at": None,
        "stale_reset_count": 0,
    }


def default_modbus_link_health_by_plant(plant_ids):
    return {plant_id: default_modbus_link_health_entry() for plant_id in plant_ids}


def publish_modbus_link_health(shared_data, plant_id, health_state):
    current = default_modbus_link_health_entry()
    if isinstance(health_state, dict):
        current.update(health_state)
    with shared_data["lock"]:
        state_map = shared_data.setdefault("modbus_link_health_by_plant", {})
        state_map[plant_id] = current
        return dict(current)


def refresh_modbus_link_health(shared_data, plant_id, endpoint_cfg, *, stale_after_s):
    endpoint = dict(endpoint_cfg or {})
    health_state = snapshot_modbus_transport_health(
        endpoint.get("host", "localhost"),
        endpoint.get("port", 502),
        stale_after_s=stale_after_s,
    )
    return publish_modbus_link_health(shared_data, plant_id, health_state)
