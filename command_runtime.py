"""Generic shared-state command queue helpers for engine command lifecycle."""

from copy import deepcopy
from queue import Full


COMMAND_HISTORY_LIMIT = 200


def prune_command_history_locked(shared_data, *, keys, limit=COMMAND_HISTORY_LIMIT):
    history_ids = shared_data.setdefault(keys["history_ids"], [])
    status_by_id = shared_data.setdefault(keys["status_by_id"], {})
    while len(history_ids) > int(limit):
        oldest_id = history_ids.pop(0)
        status_by_id.pop(oldest_id, None)


def status_snapshot(status):
    return deepcopy(status) if isinstance(status, dict) else status


def get_next_command_id_for_keys(shared_data, *, keys) -> str:
    """Allocate the next command id under shared-state lock."""
    with shared_data["lock"]:
        next_id = int(shared_data.get(keys["next_id"], 1))
        shared_data[keys["next_id"]] = next_id + 1
    return f"cmd-{next_id:06d}"


def enqueue_command_for_keys(shared_data, *, kind, payload, source, now_fn, keys, history_limit=COMMAND_HISTORY_LIMIT) -> dict:
    """Create command, persist queued status, push to queue, and return current status snapshot."""
    command_id = get_next_command_id_for_keys(shared_data, keys=keys)
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
        queue_obj = shared_data[keys["queue"]]
        status_by_id = shared_data.setdefault(keys["status_by_id"], {})
        history_ids = shared_data.setdefault(keys["history_ids"], [])
        status_by_id[command_id] = status
        history_ids.append(command_id)
        prune_command_history_locked(shared_data, keys=keys, limit=history_limit)

    try:
        queue_obj.put_nowait(command)
    except Full:
        return mark_command_finished_for_keys(
            shared_data,
            command_id,
            state="rejected",
            message="queue_full",
            finished_at=now_fn(),
            keys=keys,
        )

    with shared_data["lock"]:
        return status_snapshot(shared_data.get(keys["status_by_id"], {}).get(command_id, status))


def mark_command_running_for_keys(shared_data, command_id, *, started_at, keys) -> None:
    """Mark a command as running if it is still tracked."""
    with shared_data["lock"]:
        status = (shared_data.get(keys["status_by_id"], {}) or {}).get(command_id)
        if not isinstance(status, dict):
            return
        status["state"] = "running"
        status["started_at"] = started_at
        shared_data[keys["active_id"]] = command_id


def mark_command_finished_for_keys(
    shared_data,
    command_id,
    *,
    state,
    message=None,
    result=None,
    finished_at=None,
    keys,
) -> dict:
    """Mark a command as terminal and return the resulting status snapshot."""
    terminal_state = str(state)
    with shared_data["lock"]:
        status = (shared_data.get(keys["status_by_id"], {}) or {}).get(command_id)
        if not isinstance(status, dict):
            status = {
                "id": command_id,
                "kind": None,
                "payload": {},
                "source": None,
                "created_at": None,
                "started_at": None,
            }
            shared_data.setdefault(keys["status_by_id"], {})[command_id] = status
        status["state"] = terminal_state
        status["message"] = None if message is None else str(message)
        status["result"] = deepcopy(result)
        status["finished_at"] = finished_at
        if shared_data.get(keys["active_id"]) == command_id:
            shared_data[keys["active_id"]] = None
        return status_snapshot(status)
