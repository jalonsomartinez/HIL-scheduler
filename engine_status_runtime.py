"""Shared helpers for publishing engine queue/command health summaries."""


def default_engine_status(*, include_last_observed_refresh=False):
    status = {
        "alive": False,
        "last_loop_start": None,
        "last_loop_end": None,
        "last_exception": None,
        "active_command_id": None,
        "active_command_kind": None,
        "active_command_started_at": None,
        "last_finished_command": None,
        "queue_depth": 0,
        "queued_count": 0,
        "running_count": 0,
        "failed_recent_count": 0,
    }
    if include_last_observed_refresh:
        status["last_observed_refresh"] = None
    return status


def update_engine_status(
    shared_data,
    *,
    status_key,
    queue_key,
    status_by_id_key,
    history_ids_key,
    active_id_key,
    failed_recent_window,
    now_value=None,
    set_alive=None,
    last_loop_start=None,
    last_loop_end=None,
    last_exception=None,
    last_finished_command=None,
    extra_updates=None,
    include_last_observed_refresh=False,
):
    with shared_data["lock"]:
        status = shared_data.setdefault(
            status_key,
            default_engine_status(include_last_observed_refresh=include_last_observed_refresh),
        )
        if set_alive is not None:
            status["alive"] = bool(set_alive)
        if last_loop_start is not None:
            status["last_loop_start"] = last_loop_start
        if last_loop_end is not None:
            status["last_loop_end"] = last_loop_end
        if last_exception is not None:
            status["last_exception"] = last_exception
        if last_finished_command is not None:
            status["last_finished_command"] = last_finished_command
        if isinstance(extra_updates, dict):
            status.update(extra_updates)

        queue_obj = shared_data.get(queue_key)
        try:
            status["queue_depth"] = int(queue_obj.qsize()) if queue_obj is not None else 0
        except Exception:
            status["queue_depth"] = 0

        active_id = shared_data.get(active_id_key)
        status["active_command_id"] = active_id
        status_by_id = shared_data.get(status_by_id_key, {}) or {}
        active_status = status_by_id.get(active_id) if active_id else None
        status["active_command_kind"] = active_status.get("kind") if isinstance(active_status, dict) else None
        status["active_command_started_at"] = active_status.get("started_at") if isinstance(active_status, dict) else None

        queued_count = 0
        running_count = 0
        for cmd_status in status_by_id.values():
            if not isinstance(cmd_status, dict):
                continue
            state = str(cmd_status.get("state") or "")
            if state == "queued":
                queued_count += 1
            elif state == "running":
                running_count += 1

        failed_recent_count = 0
        history_ids = list(shared_data.get(history_ids_key, []) or [])
        for cmd_id in history_ids[-int(failed_recent_window):]:
            cmd_status = status_by_id.get(cmd_id)
            if isinstance(cmd_status, dict) and str(cmd_status.get("state") or "") in {"failed", "rejected"}:
                failed_recent_count += 1

        status["queued_count"] = int(queued_count)
        status["running_count"] = int(running_count)
        status["failed_recent_count"] = int(failed_recent_count)
        return dict(status)
