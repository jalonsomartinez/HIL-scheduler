"""Pure formatting helpers for control-engine/queue and per-plant Modbus health UI."""

from datetime import datetime


def _safe_timestamp(value):
    if value is None:
        return None
    try:
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def _truncate(text, max_chars=120):
    value = str(text or "")
    if len(value) <= max_chars:
        return value
    return value[: max(0, int(max_chars) - 3)].rstrip() + "..."


def format_age_seconds(ts, now_ts):
    ts_value = _safe_timestamp(ts)
    now_value = _safe_timestamp(now_ts)
    if ts_value is None or now_value is None:
        return "n/a"
    try:
        age_s = (now_value - ts_value).total_seconds()
    except Exception:
        return "n/a"
    if age_s < 0:
        age_s = 0.0
    return f"{age_s:.1f}s"


def _format_time(ts):
    ts_value = _safe_timestamp(ts)
    if ts_value is None:
        return "n/a"
    try:
        return ts_value.strftime("%H:%M:%S")
    except Exception:
        return str(ts_value)


def summarize_control_engine_status(engine_status, now_ts) -> str:
    status = dict(engine_status or {})
    alive = "Alive" if bool(status.get("alive")) else "Stopped"
    queue_depth = int(status.get("queue_depth", 0) or 0)
    active_id = status.get("active_command_id")
    active_kind = status.get("active_command_kind")
    active_started = status.get("active_command_started_at")
    if active_id and active_kind:
        active_age = format_age_seconds(active_started, now_ts)
        active_text = f"{active_kind} ({active_id}, {active_age})"
    elif active_id:
        active_text = str(active_id)
    else:
        active_text = "None"

    last_finished = dict(status.get("last_finished_command") or {})
    if last_finished.get("id"):
        last_text = (
            f"{last_finished.get('kind') or 'command'} {last_finished.get('state') or 'unknown'} "
            f"@ {_format_time(last_finished.get('finished_at'))}"
        )
    else:
        last_text = "None"

    text = f"Control Engine: {alive} | Queue={queue_depth} | Active={active_text} | Last={last_text}"
    last_exception = dict(status.get("last_exception") or {})
    if last_exception.get("message"):
        text += f" | Loop error: {_truncate(last_exception.get('message'), max_chars=80)}"
    return text


def summarize_control_queue_status(engine_status, backlog_high_threshold=5) -> str:
    status = dict(engine_status or {})
    queued = int(status.get("queued_count", 0) or 0)
    running = int(status.get("running_count", 0) or 0)
    recent_failed = int(status.get("failed_recent_count", 0) or 0)
    queue_depth = int(status.get("queue_depth", 0) or 0)
    text = f"Command Queue: queued={queued} running={running} recent_failed={recent_failed}"
    if queue_depth > int(backlog_high_threshold):
        text += " | Backlog: HIGH"
    return text


def summarize_plant_modbus_health(plant_observed_state, now_ts):
    observed = dict(plant_observed_state or {})
    read_status = str(observed.get("read_status") or "unknown").upper()
    age_text = format_age_seconds(observed.get("last_success"), now_ts)
    stale = bool(observed.get("stale", True))
    if stale:
        age_display = f"stale ({age_text})"
    else:
        age_display = age_text

    failures = int(observed.get("consecutive_failures", 0) or 0)
    line = f"Modbus link: {read_status} | Obs age: {age_display}"
    if failures > 0:
        line += f" | Failures: {failures}"

    lines = [line]
    last_error = dict(observed.get("last_error") or {})
    error_message = last_error.get("message") or observed.get("error")
    if error_message:
        error_code = str(last_error.get("code") or read_status.lower() or "error").upper()
        lines.append(f"Error ({error_code}): {_truncate(error_message, max_chars=120)}")
    return lines


def summarize_dispatch_write_status(dispatch_write_state, *, dispatch_enabled):
    state = dict(dispatch_write_state or {})
    enabled_text = "Sending" if bool(dispatch_enabled) else "Paused"
    attempt_status = str(state.get("last_attempt_status") or "none").upper()
    attempt_at_text = _format_time(state.get("last_attempt_at")) if state.get("last_attempt_at") else "n/a"
    source = str(state.get("last_attempt_source") or "unknown")
    line1 = f"Dispatch: {enabled_text} | Last write: {attempt_status} @ {attempt_at_text}"

    if state.get("last_attempt_p_kw") is None or state.get("last_attempt_q_kvar") is None:
        line2 = "Last P/Q: n/a | Source: n/a"
    else:
        line2 = (
            f"Last P/Q: P={float(state.get('last_attempt_p_kw')):.3f} kW, "
            f"Q={float(state.get('last_attempt_q_kvar')):.3f} kvar | Source: {source}"
        )

    lines = [line1, line2]
    scheduler_ctx = dict(state.get("last_scheduler_context") or {})
    if source == "scheduler" and scheduler_ctx:
        def _readback_state(point_prefix):
            compare_source = str(scheduler_ctx.get(f"{point_prefix}_compare_source") or "unknown")
            mismatch = scheduler_ctx.get(f"{point_prefix}_readback_mismatch")
            readback_ok = scheduler_ctx.get(f"{point_prefix}_readback_ok")
            if compare_source == "readback":
                if mismatch is True:
                    return "mismatch"
                if mismatch is False:
                    return "match"
                return "unknown"
            if compare_source == "cache_fallback":
                if readback_ok is False:
                    return "read-fail->cache"
                return "cache"
            return compare_source

        line1 += f" | RB P/Q={_readback_state('p')}/{_readback_state('q')}"
        lines[0] = line1
    if state.get("last_error"):
        lines.append(f"Dispatch error: {_truncate(state.get('last_error'), max_chars=120)}")
    return lines
