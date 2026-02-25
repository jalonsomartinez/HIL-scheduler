"""Pure helpers that map dashboard triggers to control-engine command intents."""


def command_intent_from_control_trigger(trigger_id, *, bulk_request=None):
    """Return normalized command intent dict for dashboard control triggers."""
    action_map = {
        "start-lib": ("plant.start", {"plant_id": "lib"}),
        "stop-lib": ("plant.stop", {"plant_id": "lib"}),
        "record-lib": ("plant.record_start", {"plant_id": "lib"}),
        "record-stop-lib": ("plant.record_stop", {"plant_id": "lib"}),
        "start-vrfb": ("plant.start", {"plant_id": "vrfb"}),
        "stop-vrfb": ("plant.stop", {"plant_id": "vrfb"}),
        "record-vrfb": ("plant.record_start", {"plant_id": "vrfb"}),
        "record-stop-vrfb": ("plant.record_stop", {"plant_id": "vrfb"}),
    }

    if trigger_id == "bulk-control-confirm":
        if bulk_request == "start_all":
            return {"kind": "fleet.start_all", "payload": {}}
        if bulk_request == "stop_all":
            return {"kind": "fleet.stop_all", "payload": {}}
        return None

    mapped = action_map.get(trigger_id)
    if not mapped:
        return None
    kind, payload = mapped
    return {"kind": kind, "payload": dict(payload)}


def transport_switch_intent_from_confirm(trigger_id, *, stored_mode):
    """Return transport-switch command intent and requested mode for confirm trigger."""
    if trigger_id != "transport-switch-confirm":
        return None
    current_mode = "remote" if str(stored_mode) == "remote" else "local"
    requested_mode = "local" if current_mode == "remote" else "remote"
    return {"kind": "transport.switch", "payload": {"mode": requested_mode}, "requested_mode": requested_mode}
