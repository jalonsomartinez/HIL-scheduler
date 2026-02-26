"""Pure helpers that map dashboard triggers to control-engine command intents."""


def command_intent_from_control_trigger(trigger_id, *, bulk_request=None):
    """Return normalized command intent dict for dashboard control triggers."""
    action_map = {
        "start-lib": ("plant.start", {"plant_id": "lib"}),
        "stop-lib": ("plant.stop", {"plant_id": "lib"}),
        "dispatch-enable-lib": ("plant.dispatch_enable", {"plant_id": "lib"}),
        "dispatch-disable-lib": ("plant.dispatch_disable", {"plant_id": "lib"}),
        "record-lib": ("plant.record_start", {"plant_id": "lib"}),
        "record-stop-lib": ("plant.record_stop", {"plant_id": "lib"}),
        "start-vrfb": ("plant.start", {"plant_id": "vrfb"}),
        "stop-vrfb": ("plant.stop", {"plant_id": "vrfb"}),
        "dispatch-enable-vrfb": ("plant.dispatch_enable", {"plant_id": "vrfb"}),
        "dispatch-disable-vrfb": ("plant.dispatch_disable", {"plant_id": "vrfb"}),
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


def confirmed_toggle_intent_from_request(request):
    """Map a generic confirmed-toggle request payload to a normalized control intent."""
    req = dict(request or {})
    toggle_key = str(req.get("toggle_key") or "")
    requested_side = str(req.get("requested_side") or "")
    resource_key = req.get("resource_key")

    if toggle_key == "transport":
        if requested_side == "positive":
            return {"kind": "transport.switch", "payload": {"mode": "local"}}
        if requested_side == "negative":
            return {"kind": "transport.switch", "payload": {"mode": "remote"}}
        return None

    if toggle_key == "plant_power":
        plant_id = str(resource_key or "")
        if plant_id not in {"lib", "vrfb"}:
            return None
        if requested_side == "positive":
            return {"kind": "plant.start", "payload": {"plant_id": plant_id}}
        if requested_side == "negative":
            return {"kind": "plant.stop", "payload": {"plant_id": plant_id}}
        return None

    return None
