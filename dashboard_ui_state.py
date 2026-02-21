"""Pure UI state helpers for dashboard controls."""


def resolve_runtime_transition_state(transition_state, enable_state):
    if transition_state == "starting" and enable_state == 1:
        return "running"
    if transition_state == "stopping" and enable_state == 0:
        return "stopped"
    if transition_state in {"starting", "stopping", "running", "stopped"}:
        return transition_state
    if enable_state == 1:
        return "running"
    if enable_state == 0:
        return "stopped"
    return "unknown"


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
