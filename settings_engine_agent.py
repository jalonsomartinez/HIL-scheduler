"""Runtime settings engine for manual schedule activation and API/posting commands."""

import logging
import queue
import time

import pandas as pd

import manual_schedule_manager as msm
from istentore_api import IstentoreAPI
from settings_command_runtime import (
    mark_command_finished,
    mark_command_running,
)
from shared_state import snapshot_locked
from time_utils import get_config_tz, normalize_timestamp_value, now_tz


SETTINGS_ENGINE_LOOP_PERIOD_S = 0.2
SETTINGS_ENGINE_FAILED_RECENT_WINDOW = 20


def _default_settings_engine_status():
    return {
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


def _error_payload(now_value, code, message):
    return {"timestamp": now_value, "code": str(code), "message": str(message)}


def _series_runtime_state_defaults(active=False, applied_series_df=None):
    state = "active" if bool(active) else "inactive"
    return {
        "state": state,
        "desired_state": state,
        "active": bool(active),
        "applied_series_df": msm.normalize_manual_series_df(applied_series_df),
        "last_command_id": None,
        "last_error": None,
        "last_updated": None,
        "last_success": None,
    }


def _normalize_series_rows_payload(series_rows, tz):
    rows = list(series_rows or [])
    if not rows:
        return pd.DataFrame(columns=["setpoint"])
    df = pd.DataFrame(rows)
    if "datetime" not in df.columns or "setpoint" not in df.columns:
        raise ValueError("series_rows must contain datetime and setpoint")
    # Normalize per-row to preserve clear errors for malformed payloads.
    df["datetime"] = [normalize_timestamp_value(v, tz) for v in df["datetime"].tolist()]
    df = df.dropna(subset=["datetime"]).set_index("datetime")
    df["setpoint"] = pd.to_numeric(df["setpoint"], errors="coerce")
    df = df.dropna(subset=["setpoint"])
    return msm.normalize_manual_series_df(df, timezone_name=getattr(tz, "key", str(tz)))


def _ensure_manual_runtime_state_map(shared_data):
    state_map = shared_data.setdefault("manual_series_runtime_state_by_key", {})
    series_map = shared_data.setdefault("manual_schedule_series_df_by_key", msm.default_manual_series_map())
    merge_map = shared_data.setdefault("manual_schedule_merge_enabled_by_key", msm.default_manual_merge_enabled_map(False))
    for key in msm.MANUAL_SERIES_KEYS:
        if key not in state_map or not isinstance(state_map.get(key), dict):
            state_map[key] = _series_runtime_state_defaults(
                active=bool(merge_map.get(key, False)),
                applied_series_df=series_map.get(key),
            )
        else:
            st = dict(state_map[key])
            st.setdefault("desired_state", "active" if bool(st.get("active")) else "inactive")
            st.setdefault("last_command_id", None)
            st.setdefault("last_error", None)
            st.setdefault("last_updated", None)
            st.setdefault("last_success", None)
            st.setdefault("applied_series_df", msm.normalize_manual_series_df(st.get("applied_series_df")))
            st["active"] = bool(st.get("active", False))
            if st.get("state") not in {"inactive", "activating", "active", "inactivating", "updating", "error"}:
                st["state"] = "active" if st["active"] else "inactive"
            state_map[key] = st
    return state_map


def _update_settings_engine_status(
    shared_data,
    *,
    now_value=None,
    set_alive=None,
    last_loop_start=None,
    last_loop_end=None,
    last_exception=None,
    last_finished_command=None,
):
    if now_value is None:
        now_value = pd.Timestamp.utcnow().to_pydatetime()
    with shared_data["lock"]:
        status = shared_data.setdefault("settings_engine_status", _default_settings_engine_status())
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

        queue_obj = shared_data.get("settings_command_queue")
        try:
            status["queue_depth"] = int(queue_obj.qsize()) if queue_obj is not None else 0
        except Exception:
            status["queue_depth"] = 0

        active_id = shared_data.get("settings_command_active_id")
        status["active_command_id"] = active_id
        status_by_id = shared_data.get("settings_command_status_by_id", {}) or {}
        active_status = status_by_id.get(active_id) if active_id else None
        status["active_command_kind"] = active_status.get("kind") if isinstance(active_status, dict) else None
        status["active_command_started_at"] = active_status.get("started_at") if isinstance(active_status, dict) else None

        queued_count = 0
        running_count = 0
        for cmd_status in status_by_id.values():
            if not isinstance(cmd_status, dict):
                continue
            st = str(cmd_status.get("state") or "")
            if st == "queued":
                queued_count += 1
            elif st == "running":
                running_count += 1
        history_ids = list(shared_data.get("settings_command_history_ids", []) or [])
        failed_recent = 0
        for cmd_id in history_ids[-SETTINGS_ENGINE_FAILED_RECENT_WINDOW:]:
            cmd_status = status_by_id.get(cmd_id)
            if isinstance(cmd_status, dict) and str(cmd_status.get("state") or "") in {"failed", "rejected"}:
                failed_recent += 1
        status["queued_count"] = queued_count
        status["running_count"] = running_count
        status["failed_recent_count"] = failed_recent
        return dict(status)


def _serialize_series_df_to_rows(df):
    norm = msm.normalize_manual_series_df(df)
    if norm.empty:
        return []
    rows = []
    for ts, row in norm.iterrows():
        rows.append({"datetime": pd.Timestamp(ts).isoformat(), "setpoint": float(row.get("setpoint", 0.0))})
    return rows


def _set_manual_runtime_transition(shared_data, series_key, state, *, command_id=None, desired_state=None, now_value=None, error=None):
    now_value = now_value if now_value is not None else pd.Timestamp.utcnow().to_pydatetime()
    with shared_data["lock"]:
        state_map = _ensure_manual_runtime_state_map(shared_data)
        entry = dict(state_map.get(series_key, {}))
        entry["state"] = str(state)
        if desired_state is not None:
            entry["desired_state"] = str(desired_state)
        if command_id is not None:
            entry["last_command_id"] = str(command_id)
        entry["last_updated"] = now_value
        if error is not None:
            entry["last_error"] = error
        state_map[series_key] = entry


def _apply_manual_series_command(config, shared_data, command, *, tz):
    payload = dict((command or {}).get("payload", {}) or {})
    kind = str((command or {}).get("kind", ""))
    command_id = str((command or {}).get("id", ""))
    now_value = now_tz(config)
    series_key = str(payload.get("series_key", ""))
    if series_key not in msm.MANUAL_SERIES_KEYS:
        return {"state": "rejected", "message": "invalid_series_key", "result": {"series_key": series_key}}

    with shared_data["lock"]:
        state_map = _ensure_manual_runtime_state_map(shared_data)
        current = dict(state_map.get(series_key, {}))
        current_state = str(current.get("state") or "inactive")
    if current_state in {"activating", "inactivating", "updating"}:
        return {"state": "rejected", "message": "already_transitioning", "result": {"series_key": series_key}}

    if kind == "manual.inactivate":
        _set_manual_runtime_transition(
            shared_data,
            series_key,
            "inactivating",
            command_id=command_id,
            desired_state="inactive",
            now_value=now_value,
            error=None,
        )
        with shared_data["lock"]:
            merge_map = dict(shared_data.get("manual_schedule_merge_enabled_by_key", {}))
            merge_map[series_key] = False
            shared_data["manual_schedule_merge_enabled_by_key"] = merge_map
            state_map = _ensure_manual_runtime_state_map(shared_data)
            entry = dict(state_map.get(series_key, {}))
            entry["state"] = "inactive"
            entry["desired_state"] = "inactive"
            entry["active"] = False
            entry["last_command_id"] = command_id
            entry["last_updated"] = now_value
            entry["last_success"] = now_value
            entry["last_error"] = None
            state_map[series_key] = entry
        return {"state": "succeeded", "message": None, "result": {"series_key": series_key, "active": False}}

    # manual.activate / manual.update require payload rows
    try:
        series_df = _normalize_series_rows_payload(payload.get("series_rows"), tz)
    except Exception as exc:
        error = _error_payload(now_value, "invalid_payload", str(exc))
        _set_manual_runtime_transition(
            shared_data,
            series_key,
            "error",
            command_id=command_id,
            desired_state="active",
            now_value=now_value,
            error=error,
        )
        return {"state": "rejected", "message": "invalid_payload", "result": {"series_key": series_key, "error": str(exc)}}

    if kind == "manual.update" and not bool(current.get("active", False)):
        return {"state": "rejected", "message": "not_active", "result": {"series_key": series_key}}

    transition_state = "updating" if kind == "manual.update" else "activating"
    _set_manual_runtime_transition(
        shared_data,
        series_key,
        transition_state,
        command_id=command_id,
        desired_state="active",
        now_value=now_value,
        error=None,
    )
    with shared_data["lock"]:
        series_map = dict(shared_data.get("manual_schedule_series_df_by_key", {}))
        series_map[series_key] = series_df
        shared_data["manual_schedule_series_df_by_key"] = series_map
        shared_data["manual_schedule_df_by_plant"] = msm.rebuild_manual_schedule_df_by_plant(
            series_map,
            timezone_name=config.get("TIMEZONE_NAME"),
        )
        merge_map = dict(shared_data.get("manual_schedule_merge_enabled_by_key", {}))
        merge_map[series_key] = True
        shared_data["manual_schedule_merge_enabled_by_key"] = merge_map
        state_map = _ensure_manual_runtime_state_map(shared_data)
        entry = dict(state_map.get(series_key, {}))
        entry["state"] = "active"
        entry["desired_state"] = "active"
        entry["active"] = True
        entry["applied_series_df"] = series_df
        entry["last_command_id"] = command_id
        entry["last_updated"] = now_value
        entry["last_success"] = now_value
        entry["last_error"] = None
        state_map[series_key] = entry
    return {
        "state": "succeeded",
        "message": None,
        "result": {
            "series_key": series_key,
            "active": True,
            "row_count": int(len(series_df)),
            "series_rows": _serialize_series_df_to_rows(series_df),
        },
    }


def _apply_api_connect(config, shared_data, command):
    payload = dict((command or {}).get("payload", {}) or {})
    command_id = str((command or {}).get("id", ""))
    now_value = now_tz(config)

    with shared_data["lock"]:
        api_runtime = dict(shared_data.get("api_connection_runtime", {}) or {})
        if api_runtime.get("state") == "connecting":
            return {"state": "rejected", "message": "already_connecting", "result": None}
        input_password = payload.get("password")
        if isinstance(input_password, str) and input_password.strip():
            shared_data["api_password"] = input_password
        effective_password = shared_data.get("api_password")
        shared_data["api_connection_runtime"] = {
            **api_runtime,
            "state": "connecting",
            "connected": False,
            "desired_state": "connected",
            "last_command_id": command_id,
            "last_updated": now_value,
            "last_error": None,
            "disconnect_reason": None,
        }

    if not effective_password:
        error = _error_payload(now_value, "missing_password", "No API password provided or stored.")
        with shared_data["lock"]:
            runtime = dict(shared_data.get("api_connection_runtime", {}) or {})
            runtime.update(
                {
                    "state": "error",
                    "connected": False,
                    "last_error": error,
                    "last_updated": now_value,
                    "last_command_id": command_id,
                }
            )
            shared_data["api_connection_runtime"] = runtime
        return {"state": "rejected", "message": "missing_password", "result": None}

    try:
        api = IstentoreAPI(
            base_url=config.get("ISTENTORE_BASE_URL"),
            email=config.get("ISTENTORE_EMAIL"),
            timezone_name=config.get("TIMEZONE_NAME"),
        )
        api.set_password(effective_password)
        api.login()
    except Exception as exc:
        error = _error_payload(now_value, "connect_failed", str(exc))
        with shared_data["lock"]:
            runtime = dict(shared_data.get("api_connection_runtime", {}) or {})
            runtime.update(
                {
                    "state": "error",
                    "connected": False,
                    "last_error": error,
                    "last_updated": now_value,
                    "last_probe": now_value,
                    "last_command_id": command_id,
                }
            )
            shared_data["api_connection_runtime"] = runtime
        return {"state": "failed", "message": "connect_failed", "result": {"error": str(exc)}}

    with shared_data["lock"]:
        runtime = dict(shared_data.get("api_connection_runtime", {}) or {})
        runtime.update(
            {
                "state": "connected",
                "connected": True,
                "desired_state": "connected",
                "last_error": None,
                "last_updated": now_value,
                "last_success": now_value,
                "last_probe": now_value,
                "last_command_id": command_id,
                "disconnect_reason": None,
            }
        )
        shared_data["api_connection_runtime"] = runtime
    return {"state": "succeeded", "message": None, "result": {"connected": True}}


def _apply_api_disconnect(config, shared_data, command):
    command_id = str((command or {}).get("id", ""))
    now_value = now_tz(config)
    with shared_data["lock"]:
        runtime = dict(shared_data.get("api_connection_runtime", {}) or {})
        runtime.update(
            {
                "state": "disconnecting",
                "connected": False,
                "desired_state": "disconnected",
                "last_command_id": command_id,
                "last_updated": now_value,
            }
        )
        shared_data["api_connection_runtime"] = runtime
    with shared_data["lock"]:
        runtime = dict(shared_data.get("api_connection_runtime", {}) or {})
        runtime.update(
            {
                "state": "disconnected",
                "connected": False,
                "desired_state": "disconnected",
                "disconnect_reason": "operator",
                "last_error": None,
                "last_updated": now_value,
                "last_success": now_value,
                "last_command_id": command_id,
            }
        )
        shared_data["api_connection_runtime"] = runtime
        status = dict(shared_data.get("data_fetcher_status", {}) or {})
        status["connected"] = False
        shared_data["data_fetcher_status"] = status
    return {"state": "succeeded", "message": None, "result": {"disconnected": True}}


def _apply_posting_policy(config, shared_data, command, *, enabled):
    command_id = str((command or {}).get("id", ""))
    now_value = now_tz(config)
    transition_state = "enabling" if enabled else "disabling"
    terminal_state = "enabled" if enabled else "disabled"
    with shared_data["lock"]:
        runtime = dict(shared_data.get("posting_runtime", {}) or {})
        runtime.update(
            {
                "state": transition_state,
                "policy_enabled": bool(enabled),
                "desired_state": terminal_state,
                "last_command_id": command_id,
                "last_updated": now_value,
                "last_error": None,
            }
        )
        shared_data["posting_runtime"] = runtime
        shared_data["measurement_posting_enabled"] = bool(enabled)
    with shared_data["lock"]:
        runtime = dict(shared_data.get("posting_runtime", {}) or {})
        runtime.update(
            {
                "state": terminal_state,
                "policy_enabled": bool(enabled),
                "desired_state": terminal_state,
                "last_success": now_value,
                "last_updated": now_value,
                "last_error": None,
                "last_command_id": command_id,
            }
        )
        shared_data["posting_runtime"] = runtime
    return {"state": "succeeded", "message": None, "result": {"policy_enabled": bool(enabled)}}


def _execute_settings_command(config, shared_data, command, *, tz):
    kind = str((command or {}).get("kind", ""))
    if kind in {"manual.activate", "manual.update", "manual.inactivate"}:
        return _apply_manual_series_command(config, shared_data, command, tz=tz)
    if kind == "api.connect":
        return _apply_api_connect(config, shared_data, command)
    if kind == "api.disconnect":
        return _apply_api_disconnect(config, shared_data, command)
    if kind == "posting.enable":
        return _apply_posting_policy(config, shared_data, command, enabled=True)
    if kind == "posting.disable":
        return _apply_posting_policy(config, shared_data, command, enabled=False)
    return {"state": "rejected", "message": "unsupported_command", "result": {"kind": kind}}


def _run_single_settings_cycle(config, shared_data, *, tz):
    loop_now = now_tz(config)
    _update_settings_engine_status(shared_data, now_value=loop_now, set_alive=True, last_loop_start=loop_now)
    queue_obj = snapshot_locked(shared_data, lambda data: data.get("settings_command_queue"))
    if queue_obj is None:
        _update_settings_engine_status(shared_data, now_value=loop_now, set_alive=True, last_loop_end=now_tz(config))
        return None

    try:
        command = queue_obj.get_nowait()
    except queue.Empty:
        _update_settings_engine_status(shared_data, now_value=loop_now, set_alive=True, last_loop_end=now_tz(config))
        return None

    command_id = str((command or {}).get("id", ""))
    started_at = now_tz(config)
    mark_command_running(shared_data, command_id, started_at=started_at)
    try:
        outcome = _execute_settings_command(config, shared_data, command, tz=tz)
        terminal_state = str((outcome or {}).get("state", "failed"))
        terminal_message = (outcome or {}).get("message")
        terminal_result = (outcome or {}).get("result")
    except Exception as exc:
        logging.exception("SettingsEngine: command %s failed with exception.", command_id)
        terminal_state = "failed"
        terminal_message = str(exc)
        terminal_result = None
        _update_settings_engine_status(
            shared_data,
            now_value=now_tz(config),
            set_alive=True,
            last_exception={"timestamp": now_tz(config), "message": str(exc)},
        )
    finally:
        final_status = mark_command_finished(
            shared_data,
            command_id,
            state=terminal_state,
            message=terminal_message,
            result=terminal_result,
            finished_at=now_tz(config),
        )
        _update_settings_engine_status(
            shared_data,
            now_value=now_tz(config),
            set_alive=True,
            last_finished_command={
                "id": final_status.get("id"),
                "kind": final_status.get("kind"),
                "state": final_status.get("state"),
                "finished_at": final_status.get("finished_at"),
                "message": final_status.get("message"),
            },
            last_loop_end=now_tz(config),
        )
        try:
            queue_obj.task_done()
        except Exception:
            pass
    return command_id


def settings_engine_agent(config, shared_data):
    logging.info("Settings engine agent started.")
    tz = get_config_tz(config)
    with shared_data["lock"]:
        _ensure_manual_runtime_state_map(shared_data)
        shared_data.setdefault(
            "api_connection_runtime",
            {
                "state": "disconnected",
                "connected": False,
                "desired_state": "disconnected",
                "last_command_id": None,
                "last_error": None,
                "last_updated": None,
                "last_success": None,
                "last_probe": None,
                "disconnect_reason": "startup",
            },
        )
        initial_posting_enabled = bool(
            shared_data.get("measurement_posting_enabled", config.get("ISTENTORE_POST_MEASUREMENTS_IN_API_MODE", True))
        )
        shared_data.setdefault(
            "posting_runtime",
            {
                "state": "enabled" if initial_posting_enabled else "disabled",
                "policy_enabled": initial_posting_enabled,
                "desired_state": "enabled" if initial_posting_enabled else "disabled",
                "last_command_id": None,
                "last_error": None,
                "last_updated": None,
                "last_success": None,
            },
        )
        shared_data.setdefault("settings_engine_status", _default_settings_engine_status())

    while not shared_data["shutdown_event"].is_set():
        loop_start = time.monotonic()
        try:
            _run_single_settings_cycle(config, shared_data, tz=tz)
        except Exception:
            logging.exception("SettingsEngine: unexpected loop error.")
            err_now = now_tz(config)
            _update_settings_engine_status(
                shared_data,
                now_value=err_now,
                set_alive=True,
                last_exception={"timestamp": err_now, "message": "unexpected loop error"},
                last_loop_end=err_now,
            )
        elapsed = time.monotonic() - loop_start
        time.sleep(max(0.0, SETTINGS_ENGINE_LOOP_PERIOD_S - elapsed))

    _update_settings_engine_status(shared_data, now_value=now_tz(config), set_alive=False, last_loop_end=now_tz(config))
    logging.info("Settings engine agent stopped.")
