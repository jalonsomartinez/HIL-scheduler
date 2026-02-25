"""Shared helpers for authoritative API connection runtime state publication."""

from copy import deepcopy


API_CONNECTION_TRANSITION_STATES = {"connecting", "disconnecting"}


def default_api_fetch_health():
    return {
        "state": "unknown",
        "last_success": None,
        "last_error": None,
        "last_attempt": None,
    }


def default_api_posting_health():
    return {
        "state": "unknown",
        "last_success": None,
        "last_error": None,
        "last_attempt": None,
    }


def default_api_connection_runtime():
    return {
        "state": "disconnected",
        "connected": False,
        "desired_state": "disconnected",
        "last_command_id": None,
        "last_error": None,
        "last_updated": None,
        "last_success": None,
        "last_probe": None,
        "disconnect_reason": "startup",
        "fetch_health": default_api_fetch_health(),
        "posting_health": default_api_posting_health(),
    }


def _copy_dict(value):
    return dict(value) if isinstance(value, dict) else {}


def _normalized_sub_health(existing, *, allowed_states, default_state):
    sub = {**existing} if isinstance(existing, dict) else {}
    normalized = {
        "state": str(sub.get("state") or default_state),
        "last_success": sub.get("last_success"),
        "last_error": sub.get("last_error") if isinstance(sub.get("last_error"), dict) else None,
        "last_attempt": sub.get("last_attempt"),
    }
    if normalized["state"] not in allowed_states:
        normalized["state"] = default_state
    return normalized


def _ensure_runtime_locked(shared_data):
    runtime = _copy_dict(shared_data.get("api_connection_runtime"))
    defaults = default_api_connection_runtime()
    merged = {**defaults, **runtime}
    merged["state"] = str(merged.get("state") or "disconnected")
    if merged["state"] not in {"connected", "connecting", "disconnected", "disconnecting", "error"}:
        merged["state"] = "disconnected"
    merged["desired_state"] = str(merged.get("desired_state") or "disconnected")
    if merged["desired_state"] not in {"connected", "disconnected"}:
        merged["desired_state"] = "disconnected"
    merged["connected"] = bool(merged.get("connected", False))
    merged["fetch_health"] = _normalized_sub_health(
        merged.get("fetch_health"),
        allowed_states={"unknown", "ok", "error", "disabled"},
        default_state="unknown",
    )
    merged["posting_health"] = _normalized_sub_health(
        merged.get("posting_health"),
        allowed_states={"unknown", "ok", "error", "idle", "disabled"},
        default_state="unknown",
    )
    shared_data["api_connection_runtime"] = merged
    return merged


def ensure_api_connection_runtime(shared_data):
    with shared_data["lock"]:
        runtime = _ensure_runtime_locked(shared_data)
        return deepcopy(runtime)


def _error_sort_key(err):
    if not isinstance(err, dict):
        return (0, "")
    ts = err.get("timestamp")
    try:
        # Works for datetimes / pandas timestamps.
        if ts is not None and hasattr(ts, "timestamp"):
            return (2, float(ts.timestamp()))
    except Exception:
        pass
    if ts is not None:
        return (1, str(ts))
    return (0, "")


def _choose_effective_error(runtime):
    errors = []
    fetch_err = runtime.get("fetch_health", {}).get("last_error")
    if runtime.get("fetch_health", {}).get("state") == "error" and isinstance(fetch_err, dict):
        errors.append(fetch_err)
    post_err = runtime.get("posting_health", {}).get("last_error")
    if runtime.get("posting_health", {}).get("state") == "error" and isinstance(post_err, dict):
        errors.append(post_err)
    if not errors:
        return None
    return max(errors, key=_error_sort_key)


def _recompute_effective_runtime_locked(runtime, *, now_value=None):
    raw_state = str(runtime.get("state") or "disconnected")
    desired_state = str(runtime.get("desired_state") or "disconnected")
    fetch_state = str(runtime.get("fetch_health", {}).get("state") or "unknown")
    posting_state = str(runtime.get("posting_health", {}).get("state") or "unknown")

    if raw_state in API_CONNECTION_TRANSITION_STATES:
        effective_state = raw_state
        connected = False
    elif desired_state == "disconnected":
        effective_state = "disconnected"
        connected = False
    else:
        has_error = (fetch_state == "error") or (posting_state == "error")
        effective_state = "error" if has_error else "connected"
        connected = not has_error

    previous_state = runtime.get("state")
    previous_connected = bool(runtime.get("connected", False))
    previous_last_error = runtime.get("last_error")

    runtime["state"] = effective_state
    runtime["connected"] = bool(connected)
    if effective_state == "disconnected":
        runtime["last_error"] = None
    elif effective_state == "connected":
        runtime["last_error"] = None
    elif effective_state == "error":
        runtime["last_error"] = _choose_effective_error(runtime)

    if (
        now_value is not None
        and (
            previous_state != runtime.get("state")
            or previous_connected != bool(runtime.get("connected", False))
            or previous_last_error != runtime.get("last_error")
        )
    ):
        runtime["last_updated"] = now_value
    return runtime


def recompute_api_connection_runtime(shared_data, *, now_value=None):
    with shared_data["lock"]:
        runtime = _ensure_runtime_locked(shared_data)
        _recompute_effective_runtime_locked(runtime, now_value=now_value)
        shared_data["api_connection_runtime"] = runtime
        return deepcopy(runtime)


def set_api_connection_transition(
    shared_data,
    *,
    state,
    desired_state=None,
    command_id=None,
    now_value=None,
    clear_error=False,
    disconnect_reason=None,
):
    with shared_data["lock"]:
        runtime = _ensure_runtime_locked(shared_data)
        runtime["state"] = str(state)
        if desired_state is not None:
            runtime["desired_state"] = str(desired_state)
        if command_id is not None:
            runtime["last_command_id"] = str(command_id)
        if disconnect_reason is not None:
            runtime["disconnect_reason"] = disconnect_reason
        if clear_error:
            runtime["last_error"] = None
        if now_value is not None:
            runtime["last_updated"] = now_value
        _recompute_effective_runtime_locked(runtime, now_value=now_value)
        shared_data["api_connection_runtime"] = runtime
        return deepcopy(runtime)


def complete_api_connect_probe(
    shared_data,
    *,
    success,
    now_value,
    command_id=None,
    error=None,
):
    with shared_data["lock"]:
        runtime = _ensure_runtime_locked(shared_data)
        runtime["desired_state"] = "connected"
        if command_id is not None:
            runtime["last_command_id"] = str(command_id)
        runtime["last_probe"] = now_value
        fetch_health = runtime["fetch_health"]
        fetch_health["last_attempt"] = now_value
        if success:
            fetch_health["state"] = "ok"
            fetch_health["last_success"] = now_value
            fetch_health["last_error"] = None
            runtime["last_success"] = now_value
            runtime["disconnect_reason"] = None
        else:
            err = error if isinstance(error, dict) else {"timestamp": now_value, "code": "connect_failed", "message": str(error)}
            fetch_health["state"] = "error"
            fetch_health["last_error"] = err
            runtime["disconnect_reason"] = None
        runtime["state"] = "connected" if success else "error"
        _recompute_effective_runtime_locked(runtime, now_value=now_value)
        shared_data["api_connection_runtime"] = runtime
        return deepcopy(runtime)


def complete_api_disconnect(shared_data, *, now_value, command_id=None, disconnect_reason="operator"):
    with shared_data["lock"]:
        runtime = _ensure_runtime_locked(shared_data)
        runtime["state"] = "disconnected"
        runtime["desired_state"] = "disconnected"
        runtime["disconnect_reason"] = disconnect_reason
        if command_id is not None:
            runtime["last_command_id"] = str(command_id)
        runtime["last_success"] = now_value
        runtime["fetch_health"]["state"] = "disabled"
        runtime["fetch_health"]["last_error"] = None
        runtime["posting_health"]["state"] = "disabled"
        runtime["posting_health"]["last_error"] = None
        _recompute_effective_runtime_locked(runtime, now_value=now_value)
        runtime["last_updated"] = now_value
        shared_data["api_connection_runtime"] = runtime
        return deepcopy(runtime)


def _publish_sub_health(shared_data, *, subkey, state=None, now_value=None, error=None, last_attempt=None, last_success=None):
    with shared_data["lock"]:
        runtime = _ensure_runtime_locked(shared_data)
        sub = runtime[subkey]
        if last_attempt is not None:
            sub["last_attempt"] = last_attempt
        elif now_value is not None and state in {"error", "ok"}:
            sub["last_attempt"] = now_value

        if state is not None:
            sub["state"] = str(state)
            if state in {"ok", "idle", "disabled", "unknown"}:
                if state in {"ok", "idle", "disabled"}:
                    sub["last_error"] = None
            if state == "error":
                err = error if isinstance(error, dict) else {"timestamp": now_value, "code": "error", "message": str(error or "error")}
                sub["last_error"] = err
        if last_success is not None:
            sub["last_success"] = last_success
        elif now_value is not None and state == "ok":
            sub["last_success"] = now_value

        if state == "ok":
            runtime["last_success"] = now_value
        _recompute_effective_runtime_locked(runtime, now_value=now_value)
        shared_data["api_connection_runtime"] = runtime
        return deepcopy(runtime)


def publish_api_fetch_health(shared_data, *, state=None, now_value=None, error=None, last_attempt=None, last_success=None):
    return _publish_sub_health(
        shared_data,
        subkey="fetch_health",
        state=state,
        now_value=now_value,
        error=error,
        last_attempt=last_attempt,
        last_success=last_success,
    )


def publish_api_posting_health(shared_data, *, state=None, now_value=None, error=None, last_attempt=None, last_success=None):
    return _publish_sub_health(
        shared_data,
        subkey="posting_health",
        state=state,
        now_value=now_value,
        error=error,
        last_attempt=last_attempt,
        last_success=last_success,
    )
