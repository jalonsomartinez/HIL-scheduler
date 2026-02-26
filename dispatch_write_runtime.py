"""Shared runtime helpers for publishing per-plant dispatch write status."""

from datetime import datetime


def _default_dispatch_write_status_entry():
    return {
        "sending_enabled": False,
        "last_attempt_at": None,
        "last_attempt_p_kw": None,
        "last_attempt_q_kvar": None,
        "last_attempt_source": None,
        "last_attempt_status": None,
        "last_success_at": None,
        "last_success_p_kw": None,
        "last_success_q_kvar": None,
        "last_success_source": None,
        "last_error": None,
        "last_scheduler_context": None,
    }


def default_dispatch_write_status_by_plant(plant_ids):
    return {plant_id: _default_dispatch_write_status_entry() for plant_id in plant_ids}


def publish_dispatch_write_status(
    shared_data,
    plant_id,
    *,
    sending_enabled,
    attempted_at,
    p_kw,
    q_kvar,
    source,
    status,
    error=None,
    scheduler_context=None,
):
    """Publish a dispatch write attempt and update last-success fields when applicable."""
    if attempted_at is None:
        attempted_at = datetime.utcnow()

    with shared_data["lock"]:
        status_map = shared_data.setdefault("dispatch_write_status_by_plant", {})
        prev = dict(status_map.get(plant_id, {}) or {})
        current = _default_dispatch_write_status_entry()
        current.update(prev)
        current.update(
            {
                "sending_enabled": bool(sending_enabled),
                "last_attempt_at": attempted_at,
                "last_attempt_p_kw": None if p_kw is None else float(p_kw),
                "last_attempt_q_kvar": None if q_kvar is None else float(q_kvar),
                "last_attempt_source": None if source is None else str(source),
                "last_attempt_status": str(status),
                "last_error": None if error in (None, "") else str(error),
                "last_scheduler_context": dict(scheduler_context) if isinstance(scheduler_context, dict) else None,
            }
        )
        if str(status) == "ok":
            current["last_success_at"] = attempted_at
            current["last_success_p_kw"] = None if p_kw is None else float(p_kw)
            current["last_success_q_kvar"] = None if q_kvar is None else float(q_kvar)
            current["last_success_source"] = None if source is None else str(source)
        status_map[plant_id] = current
        return dict(current)


def set_dispatch_sending_enabled(shared_data, plant_id, sending_enabled):
    """Update sending-enabled mirror without recording a new write attempt."""
    with shared_data["lock"]:
        status_map = shared_data.setdefault("dispatch_write_status_by_plant", {})
        prev = dict(status_map.get(plant_id, {}) or {})
        current = _default_dispatch_write_status_entry()
        current.update(prev)
        current["sending_enabled"] = bool(sending_enabled)
        status_map[plant_id] = current
        return dict(current)
