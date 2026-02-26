"""Shared helpers for engine command lifecycle bookkeeping."""

import logging


def run_command_with_lifecycle(
    shared_data,
    *,
    queue_obj,
    command,
    now_fn,
    execute_command_fn,
    mark_command_running_fn,
    mark_command_finished_fn,
    update_engine_status_fn,
    exception_log_prefix,
    set_last_loop_end=False,
):
    """Execute one already-dequeued command with shared lifecycle/status updates."""
    command_id = str((command or {}).get("id", ""))
    started_at = now_fn()
    mark_command_running_fn(shared_data, command_id, started_at=started_at)
    try:
        outcome = execute_command_fn(command)
        terminal_state = str((outcome or {}).get("state", "failed"))
        terminal_message = (outcome or {}).get("message")
        terminal_result = (outcome or {}).get("result")
    except Exception as exc:
        logging.exception("%s: command %s failed with exception.", exception_log_prefix, command_id)
        terminal_state = "failed"
        terminal_message = str(exc)
        terminal_result = None
        update_engine_status_fn(
            shared_data,
            now_value=now_fn(),
            set_alive=True,
            last_exception={"timestamp": now_fn(), "message": str(exc)},
        )
    finally:
        final_status = mark_command_finished_fn(
            shared_data,
            command_id,
            state=terminal_state,
            message=terminal_message,
            result=terminal_result,
            finished_at=now_fn(),
        )
        status_kwargs = {
            "now_value": now_fn(),
            "set_alive": True,
            "last_finished_command": {
                "id": final_status.get("id"),
                "kind": final_status.get("kind"),
                "state": final_status.get("state"),
                "finished_at": final_status.get("finished_at"),
                "message": final_status.get("message"),
            },
        }
        if set_last_loop_end:
            status_kwargs["last_loop_end"] = now_fn()
        update_engine_status_fn(shared_data, **status_kwargs)
        try:
            queue_obj.task_done()
        except Exception:
            pass
    return command_id
