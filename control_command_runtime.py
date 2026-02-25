"""Shared-state command queue helpers for control-engine command lifecycle."""

from command_runtime import (
    COMMAND_HISTORY_LIMIT,
    enqueue_command_for_keys,
    get_next_command_id_for_keys,
    mark_command_finished_for_keys,
    mark_command_running_for_keys,
)


CONTROL_COMMAND_KEYS = {
    "queue": "control_command_queue",
    "status_by_id": "control_command_status_by_id",
    "history_ids": "control_command_history_ids",
    "active_id": "control_command_active_id",
    "next_id": "control_command_next_id",
}
def get_next_command_id(shared_data) -> str:
    return get_next_command_id_for_keys(shared_data, keys=CONTROL_COMMAND_KEYS)


def enqueue_control_command(shared_data, *, kind, payload, source, now_fn) -> dict:
    return enqueue_command_for_keys(
        shared_data,
        kind=kind,
        payload=payload,
        source=source,
        now_fn=now_fn,
        keys=CONTROL_COMMAND_KEYS,
    )


def mark_command_running(shared_data, command_id, *, started_at) -> None:
    return mark_command_running_for_keys(shared_data, command_id, started_at=started_at, keys=CONTROL_COMMAND_KEYS)


def mark_command_finished(shared_data, command_id, *, state, message=None, result=None, finished_at=None) -> dict:
    return mark_command_finished_for_keys(
        shared_data,
        command_id,
        state=state,
        message=message,
        result=result,
        finished_at=finished_at,
        keys=CONTROL_COMMAND_KEYS,
    )
