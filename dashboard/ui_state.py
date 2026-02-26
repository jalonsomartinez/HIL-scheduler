"""Pure UI state helpers for dashboard controls."""


def resolve_runtime_transition_state(transition_state, enable_state):
    if transition_state == "starting" and enable_state == 1:
        return "running"
    if transition_state == "stopping" and enable_state == 0:
        return "stopped"
    if transition_state == "starting":
        return "starting"
    if transition_state == "stopping":
        return "stopping"
    if enable_state == 1:
        return "running"
    if enable_state == 0:
        return "stopped"
    if transition_state in {"starting", "stopping", "running", "stopped"}:
        return transition_state
    return "unknown"


def resolve_click_feedback_transition_state(
    *,
    start_click_ts_ms,
    stop_click_ts_ms,
    now_ts,
    hold_seconds=1.5,
):
    """
    Return temporary UI transition state from recent Start/Stop clicks.

    The latest click wins while its age is within the hold window.
    """
    if now_ts is None:
        return None

    latest = None
    try:
        if start_click_ts_ms is not None:
            latest = ("starting", int(start_click_ts_ms))
    except (TypeError, ValueError):
        latest = None

    try:
        if stop_click_ts_ms is not None:
            stop_ts = int(stop_click_ts_ms)
            if latest is None or stop_ts >= latest[1]:
                latest = ("stopping", stop_ts)
    except (TypeError, ValueError):
        pass

    if latest is None:
        return None

    age_s = (float(now_ts.timestamp()) * 1000.0 - float(latest[1])) / 1000.0
    if age_s < 0:
        age_s = 0.0
    if age_s <= float(hold_seconds):
        return latest[0]
    return None


def get_plant_power_toggle_state(runtime_state):
    if runtime_state == "starting":
        return {
            "positive_label": "Starting...",
            "positive_disabled": True,
            "negative_label": "Stop",
            "negative_disabled": True,
            "active_side": "positive",
        }
    if runtime_state == "running":
        return {
            "positive_label": "Running",
            "positive_disabled": True,
            "negative_label": "Stop",
            "negative_disabled": False,
            "active_side": "positive",
        }
    if runtime_state == "stopping":
        return {
            "positive_label": "Run",
            "positive_disabled": True,
            "negative_label": "Stopping...",
            "negative_disabled": True,
            "active_side": "negative",
        }
    if runtime_state == "stopped":
        return {
            "positive_label": "Run",
            "positive_disabled": False,
            "negative_label": "Stopped",
            "negative_disabled": True,
            "active_side": "negative",
        }
    return {
        "positive_label": "Run",
        "positive_disabled": False,
        "negative_label": "Stop",
        "negative_disabled": True,
        "active_side": None,
    }


def get_recording_toggle_state(recording_active, click_feedback_state=None):
    state = str(click_feedback_state or "").lower()
    if state == "starting":
        return {
            "positive_label": "Starting...",
            "positive_disabled": True,
            "negative_label": "Stop",
            "negative_disabled": True,
            "active_side": "positive",
        }
    if state == "stopping":
        return {
            "positive_label": "Record",
            "positive_disabled": True,
            "negative_label": "Stopping...",
            "negative_disabled": True,
            "active_side": "negative",
        }
    if bool(recording_active):
        return {
            "positive_label": "Recording",
            "positive_disabled": True,
            "negative_label": "Stop",
            "negative_disabled": False,
            "active_side": "positive",
        }
    return {
        "positive_label": "Record",
        "positive_disabled": False,
        "negative_label": "Stopped",
        "negative_disabled": True,
        "active_side": "negative",
    }
