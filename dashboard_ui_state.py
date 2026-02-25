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


def get_plant_control_labels_and_disabled(runtime_state, recording_active):
    if runtime_state == "starting":
        start_label = "Starting..."
        start_disabled = True
        stop_label = "Stop"
        stop_disabled = True
    elif runtime_state == "running":
        start_label = "Started"
        start_disabled = True
        stop_label = "Stop"
        stop_disabled = False
    elif runtime_state == "stopping":
        start_label = "Start"
        start_disabled = True
        stop_label = "Stopping..."
        stop_disabled = True
    elif runtime_state == "stopped":
        start_label = "Start"
        start_disabled = False
        stop_label = "Stopped"
        stop_disabled = True
    else:
        start_label = "Start"
        start_disabled = False
        stop_label = "Stop"
        stop_disabled = True

    if recording_active:
        record_label = "Recording"
        record_disabled = True
        record_stop_label = "Stop Recording"
        record_stop_disabled = False
    else:
        record_label = "Record"
        record_disabled = False
        record_stop_label = "Record Stopped"
        record_stop_disabled = True

    return (
        start_label,
        start_disabled,
        stop_label,
        stop_disabled,
        record_label,
        record_disabled,
        record_stop_label,
        record_stop_disabled,
    )
