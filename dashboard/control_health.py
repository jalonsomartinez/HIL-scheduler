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


def _format_datetime(ts):
    ts_value = _safe_timestamp(ts)
    if ts_value is None:
        return "n/a"
    try:
        return ts_value.isoformat(sep=" ", timespec="seconds")
    except Exception:
        return str(ts_value)


def _format_optional_int(value):
    if value is None:
        return "n/a"
    try:
        return str(int(value))
    except Exception:
        return "n/a"


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


def summarize_plant_modbus_health(modbus_link_state, plant_observed_state, now_ts):
    link_state = dict(modbus_link_state or {})
    observed = dict(plant_observed_state or {})
    read_status = str(observed.get("read_status") or "unknown").upper()
    observed_age_text = format_age_seconds(observed.get("last_success"), now_ts)
    stale = bool(observed.get("stale", True))
    link_health = str(link_state.get("state") or "unknown").upper()
    link_age_text = format_age_seconds(link_state.get("last_success_at"), now_ts)
    if stale:
        observed_age_display = f"stale ({observed_age_text})"
    else:
        observed_age_display = observed_age_text

    failures = int(observed.get("consecutive_failures", 0) or 0)
    waiters = int(link_state.get("waiting_count", 0) or 0)
    link_failures = int(link_state.get("consecutive_failures", 0) or 0)
    link_freshness = "n/a" if link_age_text == "n/a" else (f"stale ({link_age_text})" if link_health in {"DEGRADED", "DOWN"} else link_age_text)

    line = f"Modbus link: {link_health} | Link freshness: {link_freshness}"
    if link_failures > 0:
        line += f" | Failures: {link_failures}"
    if waiters > 0:
        line += f" | Waiters: {waiters}"

    lines = [line]
    tx_line = (
        f"Last successful Modbus tx: {_format_datetime(link_state.get('last_success_at'))} | "
        f"Reconnects: {int(link_state.get('reconnect_count', 0) or 0)}"
    )
    active_operation = str(link_state.get("active_operation") or "").strip()
    active_age_s = link_state.get("active_operation_age_s")
    if active_operation:
        if active_age_s is None:
            tx_line += f" | Active: {active_operation}"
        else:
            tx_line += f" | Active: {active_operation} ({float(active_age_s):.1f}s)"
    lines.append(tx_line)
    reset_reason = str(link_state.get("last_reset_reason") or "").strip()
    reset_at = link_state.get("last_reset_at")
    stale_reset_count = int(link_state.get("stale_reset_count", 0) or 0)
    stale_reset_threshold = link_state.get("reset_after_stale_seconds")
    reset_line = f"Last reset: {reset_reason or 'n/a'} @ {_format_datetime(reset_at)} | Stale resets: {stale_reset_count}"
    if stale_reset_threshold not in (None, "", 0, 0.0):
        reset_line += f" | Stale threshold: {float(stale_reset_threshold):.1f}s"
    lines.append(reset_line)
    observed_line = f"Last observed read result: {read_status} | Obs age: {observed_age_display}"
    if failures > 0:
        observed_line += f" | Read failures: {failures}"
    lines.append(observed_line)
    lines.append(
        "Commands: "
        f"enable={_format_optional_int(observed.get('enable_state'))} | "
        f"start_command={_format_optional_int(observed.get('start_command_state'))} | "
        f"stop_command={_format_optional_int(observed.get('stop_command_state'))}"
    )
    link_error = dict(link_state.get("last_error") or {})
    if link_error.get("message"):
        lines.append(
            f"Link error (@ {_format_datetime(link_error.get('timestamp'))}): "
            f"{_truncate(link_error.get('message'), max_chars=120)}"
        )
    last_error = dict(observed.get("last_error") or {})
    error_message = last_error.get("message") or observed.get("error")
    if error_message:
        error_code = str(last_error.get("code") or read_status.lower() or "error").upper()
        error_ts = _format_datetime(last_error.get("timestamp"))
        lines.append(f"Error ({error_code} @ {error_ts}): {_truncate(error_message, max_chars=120)}")
    return lines


def summarize_dispatch_write_status(dispatch_write_state, *, dispatch_enabled):
    state = dict(dispatch_write_state or {})
    scheduler_ctx = dict(state.get("last_scheduler_context") or {})
    enabled_text = "Sending" if bool(dispatch_enabled) else "Paused"
    attempt_status = str(state.get("last_attempt_status") or "none").upper()
    attempt_at_text = _format_time(state.get("last_attempt_at")) if state.get("last_attempt_at") else "n/a"
    source = str(state.get("last_attempt_source") or "unknown")
    line1 = f"Dispatch: {enabled_text} | Last write: {attempt_status} @ {attempt_at_text}"

    voltage_mode_active = bool(scheduler_ctx.get("voltage_mode_active"))
    voltage_setpoint_pu = scheduler_ctx.get("voltage_setpoint_pu")
    if voltage_mode_active and state.get("last_attempt_p_kw") is not None and voltage_setpoint_pu is not None:
        line2 = (
            f"Last P/V: P={float(state.get('last_attempt_p_kw')):.3f} kW, "
            f"V={float(voltage_setpoint_pu):.3f} pu | Source: {source}"
        )
    elif state.get("last_attempt_p_kw") is None or state.get("last_attempt_q_kvar") is None:
        line2 = "Last P/Q: n/a | Source: n/a"
    else:
        line2 = (
            f"Last P/Q: P={float(state.get('last_attempt_p_kw')):.3f} kW, "
            f"Q={float(state.get('last_attempt_q_kvar')):.3f} kvar | Source: {source}"
        )

    lines = [line1, line2]
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

        if voltage_mode_active:
            line1 += f" | RB P/V={_readback_state('p')}/{_readback_state('v')}"
        else:
            line1 += f" | RB P/Q={_readback_state('p')}/{_readback_state('q')}"
        lines[0] = line1
    if state.get("last_error"):
        lines.append(f"Dispatch error: {_truncate(state.get('last_error'), max_chars=120)}")
    return lines
