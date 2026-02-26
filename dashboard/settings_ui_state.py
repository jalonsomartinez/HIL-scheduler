"""Pure UI state helpers for settings-engine driven dashboard controls."""


def resolve_command_click_feedback_state(*, positive_click_ts_ms, negative_click_ts_ms, positive_state, negative_state, now_ts, hold_seconds):
    if now_ts is None:
        return None
    latest = None
    try:
        if positive_click_ts_ms is not None:
            latest = (str(positive_state), int(positive_click_ts_ms))
    except (TypeError, ValueError):
        latest = None
    try:
        if negative_click_ts_ms is not None:
            neg_ts = int(negative_click_ts_ms)
            if latest is None or neg_ts >= latest[1]:
                latest = (str(negative_state), neg_ts)
    except (TypeError, ValueError):
        pass
    if latest is None:
        return None
    age_s = (float(now_ts.timestamp()) * 1000.0 - float(latest[1])) / 1000.0
    if age_s < 0:
        age_s = 0.0
    return latest[0] if age_s <= float(hold_seconds) else None


def manual_series_display_state(server_state, click_feedback_state=None):
    if click_feedback_state in {"activating", "inactivating", "updating"}:
        return click_feedback_state
    state = str(server_state or "inactive")
    if state in {"inactive", "activating", "active", "inactivating", "updating", "error"}:
        return state
    return "inactive"


def manual_series_controls_state(display_state, *, has_draft_rows, is_dirty):
    state = str(display_state or "inactive")
    activating = state == "activating"
    inactivating = state == "inactivating"
    updating = state == "updating"
    active = state == "active"
    inactive = state == "inactive"
    error = state == "error"

    activate_disabled = activating or inactivating or updating or active or (not has_draft_rows)
    inactivate_disabled = activating or inactivating or updating or inactive
    update_disabled = activating or inactivating or updating or (not active) or (not is_dirty)

    if activating:
        status_label = "Activating..."
    elif inactivating:
        status_label = "Inactivating..."
    elif updating:
        status_label = "Updating..."
    elif active:
        status_label = "Active"
    elif error:
        status_label = "Error"
    else:
        status_label = "Inactive"

    update_label = "Updating..." if updating else "Update"
    if activating:
        activate_label = "Activating..."
    elif active or updating:
        activate_label = "Active"
    else:
        activate_label = "Activate"

    if inactivating:
        inactivate_label = "Inactivating..."
    elif inactive or activating:
        inactivate_label = "Inactive"
    else:
        inactivate_label = "Inactivate"
    return {
        "activate_disabled": bool(activate_disabled),
        "inactivate_disabled": bool(inactivate_disabled),
        "update_disabled": bool(update_disabled),
        "activate_label": activate_label,
        "inactivate_label": inactivate_label,
        "status_label": status_label,
        "update_label": update_label,
        "active_visual": active or activating or updating,
    }


def api_connection_display_state(server_state, click_feedback_state=None, *, derived_error=False):
    if click_feedback_state in {"connecting", "disconnecting"}:
        return click_feedback_state
    state = str(server_state or "disconnected")
    if derived_error and state == "connected":
        return "error"
    if state in {"connected", "connecting", "disconnecting", "disconnected", "error"}:
        return state
    return "disconnected"


def api_connection_controls_state(display_state):
    state = str(display_state or "disconnected")
    connecting = state == "connecting"
    disconnecting = state == "disconnecting"
    connected = state == "connected"
    disconnected = state == "disconnected"
    connect_disabled = connecting or disconnecting or connected
    disconnect_disabled = connecting or disconnecting or disconnected
    return {
        "connect_label": "Connecting..." if connecting else ("Connected" if connected else "Connect"),
        "disconnect_label": "Disconnecting..." if disconnecting else ("Disconnected" if disconnected else "Disconnect"),
        "connect_disabled": bool(connect_disabled),
        "disconnect_disabled": bool(disconnect_disabled),
    }


def posting_display_state(server_state, click_feedback_state=None):
    if click_feedback_state in {"enabling", "disabling"}:
        return click_feedback_state
    state = str(server_state or "disabled")
    if state in {"enabled", "enabling", "disabled", "disabling", "error"}:
        return state
    return "disabled"


def posting_controls_state(display_state):
    state = str(display_state or "disabled")
    enabling = state == "enabling"
    disabling = state == "disabling"
    enabled = state == "enabled"
    disabled = state == "disabled"
    return {
        "enable_label": "Enabling..." if enabling else ("Enabled" if enabled else "Enable"),
        "disable_label": "Disabling..." if disabling else ("Disabled" if disabled else "Disable"),
        "enable_disabled": enabling or disabling or enabled,
        "disable_disabled": enabling or disabling or disabled,
    }
