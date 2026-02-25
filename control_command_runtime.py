"""Shared-state command queue helpers for control-engine command lifecycle."""

from copy import deepcopy
from queue import Full


COMMAND_HISTORY_LIMIT = 200


def _prune_command_history_locked(shared_data, limit=COMMAND_HISTORY_LIMIT):
    history_ids = shared_data.setdefault("control_command_history_ids", [])
    status_by_id = shared_data.setdefault("control_command_status_by_id", {})
    while len(history_ids) > int(limit):
        oldest_id = history_ids.pop(0)
        status_by_id.pop(oldest_id, None)


def _status_snapshot(status):
    return deepcopy(status) if isinstance(status, dict) else status


def get_next_command_id(shared_data) -> str:
    """Allocate the next command id under shared-state lock."""
    with shared_data["lock"]:
        next_id = int(shared_data.get("control_command_next_id", 1))
        shared_data["control_command_next_id"] = next_id + 1
    return f"cmd-{next_id:06d}"


def enqueue_control_command(shared_data, *, kind, payload, source, now_fn) -> dict:
    """Create command, persist queued status, push to queue, and return current status snapshot."""
    command_id = get_next_command_id(shared_data)
    created_at = now_fn()
    command = {
        "id": command_id,
        "kind": str(kind),
        "payload": dict(payload or {}),
        "source": str(source or "unknown"),
        "created_at": created_at,
    }
    status = {
        "id": command_id,
        "kind": command["kind"],
        "payload": deepcopy(command["payload"]),
        "source": command["source"],
        "state": "queued",
        "message": None,
        "result": None,
        "created_at": created_at,
        "started_at": None,
        "finished_at": None,
    }

    with shared_data["lock"]:
        queue_obj = shared_data["control_command_queue"]
        status_by_id = shared_data.setdefault("control_command_status_by_id", {})
        history_ids = shared_data.setdefault("control_command_history_ids", [])
        status_by_id[command_id] = status
        history_ids.append(command_id)
        _prune_command_history_locked(shared_data)

    try:
        queue_obj.put_nowait(command)
    except Full:
        return mark_command_finished(
            shared_data,
            command_id,
            state="rejected",
            message="queue_full",
            finished_at=now_fn(),
        )

    with shared_data["lock"]:
        return _status_snapshot(shared_data.get("control_command_status_by_id", {}).get(command_id, status))


def mark_command_running(shared_data, command_id, *, started_at) -> None:
    """Mark a command as running if it is still tracked."""
    with shared_data["lock"]:
        status = (shared_data.get("control_command_status_by_id", {}) or {}).get(command_id)
        if not isinstance(status, dict):
            return
        status["state"] = "running"
        status["started_at"] = started_at
        shared_data["control_command_active_id"] = command_id


def mark_command_finished(shared_data, command_id, *, state, message=None, result=None, finished_at=None) -> dict:
    """Mark a command as terminal and return the resulting status snapshot."""
    terminal_state = str(state)
    with shared_data["lock"]:
        status = (shared_data.get("control_command_status_by_id", {}) or {}).get(command_id)
        if not isinstance(status, dict):
            status = {
                "id": command_id,
                "kind": None,
                "payload": {},
                "source": None,
                "created_at": None,
                "started_at": None,
            }
            shared_data.setdefault("control_command_status_by_id", {})[command_id] = status
        status["state"] = terminal_state
        status["message"] = None if message is None else str(message)
        status["result"] = deepcopy(result)
        status["finished_at"] = finished_at
        if shared_data.get("control_command_active_id") == command_id:
            shared_data["control_command_active_id"] = None
        return _status_snapshot(status)
