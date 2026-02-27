"""Runtime control engine for dashboard-issued commands and plant observed-state cache."""

import logging
import os
import queue
import time

import pandas as pd
from pyModbusTCP.client import ModbusClient

import scheduling.manual_schedule_manager as msm
from control.command_runtime import mark_command_finished, mark_command_running
from runtime.dispatch_write_runtime import publish_dispatch_write_status, set_dispatch_sending_enabled
from control.flows import (
    perform_transport_switch as perform_transport_switch_flow,
    safe_stop_all_plants as safe_stop_all_plants_flow,
    safe_stop_plant as safe_stop_plant_flow,
)
from control.modbus_io import (
    read_enable_state as read_enable_state_io,
    send_setpoints as send_setpoints_io,
    set_enable as set_enable_io,
    wait_until_battery_power_below_threshold as wait_until_battery_power_below_threshold_io,
)
from runtime.engine_command_cycle_runtime import run_command_with_lifecycle
from runtime.engine_status_runtime import default_engine_status, update_engine_status
from measurement.storage import find_latest_persisted_soc_for_plant
from modbus.codec import read_point_internal
from runtime.contracts import resolve_modbus_endpoint, sanitize_plant_name
from runtime.paths import get_data_dir
from scheduling.runtime import build_effective_schedule_frame, resolve_schedule_setpoint
from runtime.shared_state import snapshot_locked
from time_utils import get_config_tz, now_tz


CONTROL_ENGINE_LOOP_PERIOD_S = 1.0
OBSERVED_STATE_STALE_AFTER_S = 3.0
CONTROL_ENGINE_FAILED_RECENT_WINDOW = 20


def _plant_name(config, plant_id):
    plants_cfg = config.get("PLANTS", {})
    return str((plants_cfg.get(plant_id, {}) or {}).get("name", plant_id.upper()))


def _get_plant_modbus_config(config, shared_data, plant_id, transport_mode=None):
    mode = transport_mode or snapshot_locked(shared_data, lambda data: data.get("transport_mode", "local"))
    endpoint = resolve_modbus_endpoint(config, plant_id, mode)
    return {
        "mode": mode,
        "host": endpoint.get("host", "localhost"),
        "port": int(endpoint.get("port", 5020 if plant_id == "lib" else 5021)),
        "byte_order": endpoint.get("byte_order"),
        "word_order": endpoint.get("word_order"),
        "points": endpoint.get("points", {}),
    }


def _set_enable(config, shared_data, plant_id, value):
    cfg = _get_plant_modbus_config(config, shared_data, plant_id)
    return set_enable_io(cfg, plant_id.upper(), value)


def _send_setpoints(config, shared_data, plant_id, p_kw, q_kvar):
    cfg = _get_plant_modbus_config(config, shared_data, plant_id)
    return send_setpoints_io(cfg, plant_id.upper(), p_kw, q_kvar)


def _read_enable_state(config, shared_data, plant_id, transport_mode=None):
    cfg = _get_plant_modbus_config(config, shared_data, plant_id, transport_mode=transport_mode)
    return read_enable_state_io(cfg)


def _wait_until_battery_power_below_threshold(
    config,
    shared_data,
    plant_id,
    threshold_kw=1.0,
    timeout_s=30,
    *,
    fail_fast_on_connect_failure=True,
):
    cfg = _get_plant_modbus_config(config, shared_data, plant_id)
    return wait_until_battery_power_below_threshold_io(
        cfg,
        threshold_kw=threshold_kw,
        timeout_s=timeout_s,
        fail_fast_on_connect_failure=fail_fast_on_connect_failure,
    )


def _read_observed_points(config, shared_data, plant_id, transport_mode=None):
    cfg = _get_plant_modbus_config(config, shared_data, plant_id, transport_mode=transport_mode)
    client = ModbusClient(host=cfg["host"], port=cfg["port"])
    values = {"enable_state": None, "p_battery_kw": None, "q_battery_kvar": None}
    error = None
    try:
        if not client.open():
            return values, {
                "code": "connect_failed",
                "message": f"Could not connect to {plant_id.upper()} endpoint.",
            }
        enable_state = read_point_internal(client, cfg, "enable")
        p_battery = read_point_internal(client, cfg, "p_battery")
        q_battery = read_point_internal(client, cfg, "q_battery")
        values["enable_state"] = None if enable_state is None else int(enable_state)
        values["p_battery_kw"] = None if p_battery is None else float(p_battery)
        values["q_battery_kvar"] = None if q_battery is None else float(q_battery)
    except Exception as exc:
        error = {"code": "read_error", "message": str(exc)}
    finally:
        try:
            client.close()
        except Exception:
            pass
    return values, error


def _publish_observed_state(shared_data, plant_id, values, *, error=None, now_value=None, stale_after_s=OBSERVED_STATE_STALE_AFTER_S):
    values = dict(values or {})
    now_value = now_value if now_value is not None else pd.Timestamp.utcnow().to_pydatetime()

    with shared_data["lock"]:
        state_map = shared_data.setdefault("plant_observed_state_by_plant", {})
        prev = dict(state_map.get(plant_id, {}) or {})
        current = {
            "enable_state": prev.get("enable_state"),
            "p_battery_kw": prev.get("p_battery_kw"),
            "q_battery_kvar": prev.get("q_battery_kvar"),
            "last_attempt": now_value,
            "last_success": prev.get("last_success"),
            "error": prev.get("error"),
            "read_status": str(prev.get("read_status", "unknown") or "unknown"),
            "last_error": prev.get("last_error"),
            "consecutive_failures": int(prev.get("consecutive_failures", 0) or 0),
            "stale": True,
        }

        success_any = any(values.get(key) is not None for key in ("enable_state", "p_battery_kw", "q_battery_kvar"))
        if success_any:
            for key in ("enable_state", "p_battery_kw", "q_battery_kvar"):
                if values.get(key) is not None:
                    current[key] = values.get(key)
            current["last_success"] = now_value
            current["error"] = None
            current["read_status"] = "ok"
            current["last_error"] = prev.get("last_error")
            current["consecutive_failures"] = 0
        elif error is not None:
            if isinstance(error, dict):
                error_code = str(error.get("code") or "read_error")
                error_message = str(error.get("message") or error_code)
            else:
                error_message = str(error)
                error_code = "connect_failed" if error_message.lower().startswith("connect_failed") else "read_error"
            current["error"] = error_message
            current["read_status"] = error_code
            current["last_error"] = {
                "timestamp": now_value,
                "code": error_code,
                "message": error_message,
            }
            current["consecutive_failures"] = int(prev.get("consecutive_failures", 0) or 0) + 1

        last_success = current.get("last_success")
        stale = True
        try:
            if last_success is not None:
                stale = (now_value - last_success).total_seconds() > float(stale_after_s)
        except Exception:
            stale = True
        current["stale"] = bool(stale)
        state_map[plant_id] = current
        plant_operating_state_map = shared_data.setdefault("plant_operating_state_by_plant", {})
        if bool(current["stale"]):
            plant_operating_state_map[plant_id] = "unknown"
        elif current.get("enable_state") == 1:
            plant_operating_state_map[plant_id] = "running"
        elif current.get("enable_state") == 0:
            plant_operating_state_map[plant_id] = "stopped"
        else:
            plant_operating_state_map[plant_id] = "unknown"
        return dict(current)


def _default_control_engine_status():
    return default_engine_status(include_last_observed_refresh=True)


def _update_control_engine_status(
    shared_data,
    *,
    now_value=None,
    set_alive=None,
    last_loop_start=None,
    last_loop_end=None,
    last_observed_refresh=None,
    last_exception=None,
    last_finished_command=None,
):
    if now_value is None:
        now_value = pd.Timestamp.utcnow().to_pydatetime()
    extra_updates = {}
    if last_observed_refresh is not None:
        extra_updates["last_observed_refresh"] = last_observed_refresh
    return update_engine_status(
        shared_data,
        status_key="control_engine_status",
        queue_key="control_command_queue",
        status_by_id_key="control_command_status_by_id",
        history_ids_key="control_command_history_ids",
        active_id_key="control_command_active_id",
        failed_recent_window=CONTROL_ENGINE_FAILED_RECENT_WINDOW,
        now_value=now_value,
        set_alive=set_alive,
        last_loop_start=last_loop_start,
        last_loop_end=last_loop_end,
        last_exception=last_exception,
        last_finished_command=last_finished_command,
        extra_updates=extra_updates or None,
        include_last_observed_refresh=True,
    )


def _refresh_all_observed_state(
    config,
    shared_data,
    plant_ids,
    *,
    now_value=None,
    read_observed_points_fn=_read_observed_points,
):
    if now_value is None:
        now_value = now_tz(config)
    transport_mode = snapshot_locked(shared_data, lambda data: data.get("transport_mode", "local"))
    results = {}
    for plant_id in plant_ids:
        values, error = read_observed_points_fn(config, shared_data, plant_id, transport_mode=transport_mode)
        results[plant_id] = _publish_observed_state(shared_data, plant_id, values, error=error, now_value=now_value)
    return results


def _get_daily_recording_file_path(config, plant_id):
    safe_name = sanitize_plant_name(_plant_name(config, plant_id), plant_id)
    date_str = now_tz(config).strftime("%Y%m%d")
    return os.path.join(get_data_dir(__file__), f"{date_str}_{safe_name}.csv")


def _clamp_soc_pu(value, fallback):
    try:
        soc_value = float(value)
    except (TypeError, ValueError):
        soc_value = float(fallback)
    if pd.isna(soc_value):
        soc_value = float(fallback)
    return min(1.0, max(0.0, soc_value))


def _resolve_local_start_soc_seed(config, shared_data, plant_id, tz):
    startup_initial_soc_pu = float(config.get("STARTUP_INITIAL_SOC_PU", 0.5))
    latest = find_latest_persisted_soc_for_plant(get_data_dir(__file__), _plant_name(config, plant_id), plant_id, tz)
    if latest is not None:
        logging.info(
            "ControlEngine: %s local start SoC seed from disk %.4f pu (%s @ %s).",
            plant_id.upper(),
            float(latest["soc_pu"]),
            latest["file_path"],
            pd.Timestamp(latest["timestamp"]).isoformat(),
        )
        return {
            "soc_pu": _clamp_soc_pu(latest["soc_pu"], startup_initial_soc_pu),
            "source": "disk",
            "message": f"{latest['file_path']}",
        }

    fallback_soc = _clamp_soc_pu(startup_initial_soc_pu, startup_initial_soc_pu)
    logging.info(
        "ControlEngine: %s local start SoC seed not found on disk; using startup fallback %.4f pu.",
        plant_id.upper(),
        fallback_soc,
    )
    return {
        "soc_pu": fallback_soc,
        "source": "startup_fallback",
        "message": "no persisted soc found",
    }


def _request_local_emulator_soc_seed(shared_data, plant_id, soc_pu, source, *, timeout_s=1.5):
    request_id = int(time.time_ns())
    request_payload = {
        "request_id": request_id,
        "soc_pu": _clamp_soc_pu(soc_pu, 0.5),
        "source": str(source),
    }

    with shared_data["lock"]:
        request_map = shared_data.setdefault("local_emulator_soc_seed_request_by_plant", {})
        result_map = shared_data.setdefault("local_emulator_soc_seed_result_by_plant", {})
        request_map[plant_id] = dict(request_payload)
        result_map.setdefault(plant_id, {"request_id": None, "status": "idle", "soc_pu": None, "message": None})

    logging.info(
        "ControlEngine: %s local emulator SoC seed request published (id=%s source=%s soc=%.4f pu).",
        plant_id.upper(),
        request_id,
        request_payload["source"],
        request_payload["soc_pu"],
    )

    deadline = time.monotonic() + max(0.1, float(timeout_s))
    while time.monotonic() < deadline:
        result = snapshot_locked(
            shared_data,
            lambda data: dict((data.get("local_emulator_soc_seed_result_by_plant", {}) or {}).get(plant_id, {})),
        )
        if result and result.get("request_id") == request_id:
            status = str(result.get("status", ""))
            if status in {"applied", "skipped", "error"}:
                if status == "applied":
                    logging.info(
                        "ControlEngine: %s local emulator SoC seed applied (id=%s soc=%.4f pu).",
                        plant_id.upper(),
                        request_id,
                        float(result.get("soc_pu", request_payload["soc_pu"])),
                    )
                else:
                    logging.warning(
                        "ControlEngine: %s local emulator SoC seed %s (id=%s message=%s).",
                        plant_id.upper(),
                        status,
                        request_id,
                        result.get("message"),
                    )
                return result
        time.sleep(0.05)

    logging.warning(
        "ControlEngine: %s local emulator SoC seed request timed out (id=%s, continuing start).",
        plant_id.upper(),
        request_id,
    )
    return None


def _get_latest_schedule_setpoint(config, shared_data, plant_id, tz):
    source_snapshot = snapshot_locked(
        shared_data,
        lambda data: {
            "api_df": data.get("api_schedule_df_by_plant", {}).get(plant_id),
            "manual_series_map": dict(data.get("manual_schedule_series_df_by_key", {})),
            "manual_merge_enabled": dict(data.get("manual_schedule_merge_enabled_by_key", {})),
        },
    )
    p_key, q_key = msm.manual_series_keys_for_plant(plant_id)
    effective_df = build_effective_schedule_frame(
        source_snapshot["api_df"],
        source_snapshot["manual_series_map"].get(p_key),
        source_snapshot["manual_series_map"].get(q_key),
        manual_p_enabled=bool(source_snapshot["manual_merge_enabled"].get(p_key, False)),
        manual_q_enabled=bool(source_snapshot["manual_merge_enabled"].get(q_key, False)),
        tz=tz,
    )
    p_kw, q_kvar, _ = resolve_schedule_setpoint(
        effective_df,
        now_tz(config),
        tz,
        source="manual",
    )
    return p_kw, q_kvar


def _safe_stop_plant(config, shared_data, plant_id, *, threshold_kw=1.0, timeout_s=30):
    def _send_and_publish(pid, p_kw, q_kvar):
        ok = bool(_send_setpoints(config, shared_data, pid, p_kw, q_kvar))
        gate = snapshot_locked(shared_data, lambda data: bool((data.get("scheduler_running_by_plant", {}) or {}).get(pid, False)))
        publish_dispatch_write_status(
            shared_data,
            pid,
            sending_enabled=gate,
            attempted_at=now_tz(config),
            p_kw=p_kw,
            q_kvar=q_kvar,
            source="control_engine.safe_stop",
            status="ok" if ok else "failed",
            error=None if ok else "setpoint_write_failed",
        )
        return ok

    return safe_stop_plant_flow(
        shared_data,
        plant_id,
        send_setpoints=_send_and_publish,
        wait_until_battery_power_below_threshold=lambda pid, threshold_kw=1.0, timeout_s=30: _wait_until_battery_power_below_threshold(
            config,
            shared_data,
            pid,
            threshold_kw=threshold_kw,
            timeout_s=timeout_s,
            fail_fast_on_connect_failure=True,
        ),
        set_enable=lambda pid, value: _set_enable(config, shared_data, pid, value),
        threshold_kw=threshold_kw,
        timeout_s=timeout_s,
    )


def _safe_stop_all_plants(config, shared_data, plant_ids):
    return safe_stop_all_plants_flow(
        plant_ids,
        lambda pid: _safe_stop_plant(config, shared_data, pid),
    )


def _start_one_plant(
    config,
    shared_data,
    plant_id,
    *,
    tz,
    now_fn=now_tz,
    set_enable_fn=None,
    send_setpoints_fn=None,
    get_latest_schedule_setpoint_fn=None,
    resolve_local_start_soc_seed_fn=None,
    request_local_emulator_soc_seed_fn=None,
):
    set_enable_fn = set_enable_fn or (lambda pid, value: _set_enable(config, shared_data, pid, value))
    send_setpoints_fn = send_setpoints_fn or (lambda pid, p, q: _send_setpoints(config, shared_data, pid, p, q))
    get_latest_schedule_setpoint_fn = get_latest_schedule_setpoint_fn or (
        lambda pid: _get_latest_schedule_setpoint(config, shared_data, pid, tz)
    )
    resolve_local_start_soc_seed_fn = resolve_local_start_soc_seed_fn or (
        lambda pid: _resolve_local_start_soc_seed(config, shared_data, pid, tz)
    )
    request_local_emulator_soc_seed_fn = request_local_emulator_soc_seed_fn or (
        lambda pid, soc_pu, source: _request_local_emulator_soc_seed(shared_data, pid, soc_pu, source)
    )

    with shared_data["lock"]:
        transition_state = shared_data.get("plant_transition_by_plant", {}).get(plant_id, "stopped")
        dispatch_enabled = bool(shared_data.get("scheduler_running_by_plant", {}).get(plant_id, False))
        if transition_state in {"starting", "running"}:
            logging.info("ControlEngine: %s start ignored (state=%s).", plant_id.upper(), transition_state)
            return {"state": "rejected", "message": "already_running", "result": {"transition_state": transition_state}}
        shared_data["plant_transition_by_plant"][plant_id] = "starting"

    seed_result = None
    transport_mode = snapshot_locked(shared_data, lambda data: data.get("transport_mode", "local"))
    if transport_mode == "local":
        seed = resolve_local_start_soc_seed_fn(plant_id)
        seed_result = request_local_emulator_soc_seed_fn(
            plant_id,
            (seed or {}).get("soc_pu"),
            (seed or {}).get("source", "unknown"),
        )

    enabled = bool(set_enable_fn(plant_id, 1))
    if not enabled:
        logging.error("ControlEngine: %s start failed while enabling plant.", plant_id.upper())
        with shared_data["lock"]:
            shared_data["plant_transition_by_plant"][plant_id] = "stopped"
        return {
            "state": "failed",
            "message": "enable_failed",
            "result": {
                "enable_ok": False,
                "initial_setpoint_write_ok": False,
                "initial_p_kw": 0.0,
                "initial_q_kvar": 0.0,
                "seed_result": seed_result,
                "dispatch_enabled": bool(dispatch_enabled),
            },
        }

    p_kw, q_kvar = get_latest_schedule_setpoint_fn(plant_id)
    if dispatch_enabled:
        send_ok = bool(send_setpoints_fn(plant_id, p_kw, q_kvar))
        publish_dispatch_write_status(
            shared_data,
            plant_id,
            sending_enabled=True,
            attempted_at=now_fn(config),
            p_kw=p_kw,
            q_kvar=q_kvar,
            source="control_engine.start",
            status="ok" if send_ok else "failed",
            error=None if send_ok else "setpoint_write_failed",
        )
        if send_ok:
            logging.info(
                "ControlEngine: %s initial setpoints sent (P=%.3f kW Q=%.3f kvar).",
                plant_id.upper(),
                p_kw,
                q_kvar,
            )
        else:
            logging.warning(
                "ControlEngine: %s initial setpoint write failed (P=%.3f kW Q=%.3f kvar).",
                plant_id.upper(),
                p_kw,
                q_kvar,
            )
    else:
        send_ok = False
        publish_dispatch_write_status(
            shared_data,
            plant_id,
            sending_enabled=False,
            attempted_at=now_fn(config),
            p_kw=p_kw,
            q_kvar=q_kvar,
            source="control_engine.start",
            status="skipped",
            error="dispatch_paused",
        )
        logging.info(
            "ControlEngine: %s initial setpoint write skipped because dispatch is paused (P=%.3f kW Q=%.3f kvar).",
            plant_id.upper(),
            p_kw,
            q_kvar,
        )

    with shared_data["lock"]:
        shared_data["plant_transition_by_plant"][plant_id] = "running"

    return {
        "state": "succeeded",
        "message": None,
        "result": {
            "enable_ok": True,
            "initial_setpoint_write_ok": bool(send_ok),
            "initial_p_kw": float(p_kw),
            "initial_q_kvar": float(q_kvar),
            "seed_result": seed_result,
            "dispatch_enabled": bool(dispatch_enabled),
            "initial_setpoint_write_skipped": (not bool(dispatch_enabled)),
        },
    }


def _stop_one_plant(config, shared_data, plant_id, *, safe_stop_plant_fn=None):
    safe_stop_plant_fn = safe_stop_plant_fn or (lambda pid: _safe_stop_plant(config, shared_data, pid))
    with shared_data["lock"]:
        transition_state = shared_data.get("plant_transition_by_plant", {}).get(plant_id, "stopped")
        if transition_state in {"stopping", "stopped"}:
            logging.info("ControlEngine: %s stop ignored (state=%s).", plant_id.upper(), transition_state)
            return {"state": "rejected", "message": "already_stopped", "result": {"transition_state": transition_state}}
        shared_data["plant_transition_by_plant"][plant_id] = "stopping"

    result = dict(safe_stop_plant_fn(plant_id) or {})
    if not result.get("disable_ok", False):
        with shared_data["lock"]:
            shared_data["plant_transition_by_plant"][plant_id] = "unknown"
    return {
        "state": "succeeded" if bool(result.get("disable_ok", False)) else "failed",
        "message": None if bool(result.get("disable_ok", False)) else "disable_failed",
        "result": result,
    }


def _execute_command(config, shared_data, command, *, plant_ids, tz, now_fn=now_tz, deps=None):
    deps = dict(deps or {})

    def _dep(name, default):
        return deps.get(name) or default

    safe_stop_plant_fn = _dep("safe_stop_plant_fn", lambda pid: _safe_stop_plant(config, shared_data, pid))
    safe_stop_all_fn = _dep("safe_stop_all_plants_fn", lambda: _safe_stop_all_plants(config, shared_data, plant_ids))
    perform_transport_switch_fn = _dep(
        "perform_transport_switch_fn",
        lambda requested_mode: perform_transport_switch_flow(shared_data, plant_ids, requested_mode, safe_stop_all_fn),
    )
    start_one_plant_fn = _dep(
        "start_one_plant_fn",
        lambda pid: _start_one_plant(config, shared_data, pid, tz=tz, now_fn=now_fn),
    )
    stop_one_plant_fn = _dep("stop_one_plant_fn", lambda pid: _stop_one_plant(config, shared_data, pid, safe_stop_plant_fn=safe_stop_plant_fn))
    get_daily_recording_file_path_fn = _dep("get_daily_recording_file_path_fn", lambda pid: _get_daily_recording_file_path(config, pid))

    kind = str((command or {}).get("kind", ""))
    payload = dict((command or {}).get("payload", {}) or {})

    if kind == "plant.start":
        plant_id = str(payload.get("plant_id", ""))
        return start_one_plant_fn(plant_id)

    if kind == "plant.stop":
        plant_id = str(payload.get("plant_id", ""))
        return stop_one_plant_fn(plant_id)

    if kind == "plant.dispatch_enable":
        plant_id = str(payload.get("plant_id", ""))
        with shared_data["lock"]:
            previous = bool(shared_data.get("scheduler_running_by_plant", {}).get(plant_id, False))
            shared_data["scheduler_running_by_plant"][plant_id] = True
        set_dispatch_sending_enabled(shared_data, plant_id, True)
        return {"state": "succeeded", "message": None, "result": {"previous": previous, "current": True}}

    if kind == "plant.dispatch_disable":
        plant_id = str(payload.get("plant_id", ""))
        with shared_data["lock"]:
            previous = bool(shared_data.get("scheduler_running_by_plant", {}).get(plant_id, False))
            shared_data["scheduler_running_by_plant"][plant_id] = False
        set_dispatch_sending_enabled(shared_data, plant_id, False)
        return {"state": "succeeded", "message": None, "result": {"previous": previous, "current": False}}

    if kind == "plant.record_start":
        plant_id = str(payload.get("plant_id", ""))
        os.makedirs(get_data_dir(__file__), exist_ok=True)
        file_path = get_daily_recording_file_path_fn(plant_id)
        with shared_data["lock"]:
            current = shared_data.get("measurements_filename_by_plant", {}).get(plant_id)
            if current == file_path:
                return {"state": "succeeded", "message": None, "result": {"noop": True, "file_path": file_path}}
            shared_data["measurements_filename_by_plant"][plant_id] = file_path
        return {"state": "succeeded", "message": None, "result": {"noop": False, "file_path": file_path}}

    if kind == "plant.record_stop":
        plant_id = str(payload.get("plant_id", ""))
        with shared_data["lock"]:
            current = shared_data.get("measurements_filename_by_plant", {}).get(plant_id)
            if current is None:
                return {"state": "succeeded", "message": None, "result": {"noop": True}}
            shared_data["measurements_filename_by_plant"][plant_id] = None
        return {"state": "succeeded", "message": None, "result": {"noop": False}}

    if kind == "fleet.start_all":
        os.makedirs(get_data_dir(__file__), exist_ok=True)
        with shared_data["lock"]:
            for pid in plant_ids:
                shared_data["measurements_filename_by_plant"][pid] = get_daily_recording_file_path_fn(pid)
        per_plant = {}
        any_failed = False
        for pid in plant_ids:
            with shared_data["lock"]:
                shared_data["scheduler_running_by_plant"][pid] = True
            set_dispatch_sending_enabled(shared_data, pid, True)
            sub = dict(start_one_plant_fn(pid) or {})
            per_plant[pid] = sub
            sub_state = str(sub.get("state", "failed"))
            sub_message = str(sub.get("message", ""))
            if sub_state == "failed":
                any_failed = True
            elif sub_state == "rejected" and sub_message != "already_running":
                any_failed = True
        return {
            "state": "failed" if any_failed else "succeeded",
            "message": None if not any_failed else "fleet_start_partial_failure",
            "result": {"per_plant": per_plant},
        }

    if kind == "fleet.stop_all":
        results = dict(safe_stop_all_fn() or {})
        for pid in plant_ids:
            set_dispatch_sending_enabled(shared_data, pid, False)
        with shared_data["lock"]:
            for pid in plant_ids:
                shared_data["measurements_filename_by_plant"][pid] = None
        all_disable_ok = all(bool((results.get(pid) or {}).get("disable_ok", False)) for pid in plant_ids)
        return {
            "state": "succeeded" if all_disable_ok else "failed",
            "message": None if all_disable_ok else "fleet_stop_partial_failure",
            "result": {"per_plant": results},
        }

    if kind == "transport.switch":
        requested_mode = str(payload.get("mode", "local"))
        current_mode = snapshot_locked(shared_data, lambda data: data.get("transport_mode", "local"))
        if requested_mode == current_mode:
            return {"state": "succeeded", "message": None, "result": {"noop": True, "requested_mode": requested_mode}}
        perform_transport_switch_fn(requested_mode)
        updated_mode = snapshot_locked(shared_data, lambda data: data.get("transport_mode", "local"))
        ok = updated_mode == requested_mode
        return {
            "state": "succeeded" if ok else "failed",
            "message": None if ok else "transport_switch_failed",
            "result": {"noop": False, "requested_mode": requested_mode, "transport_mode": updated_mode},
        }

    return {"state": "rejected", "message": "unsupported_command", "result": {"kind": kind}}


def _run_single_engine_cycle(config, shared_data, *, plant_ids, tz, deps=None, now_fn=now_tz):
    deps = dict(deps or {})
    refresh_fn = deps.get("refresh_all_observed_state_fn") or (
        lambda: _refresh_all_observed_state(config, shared_data, plant_ids, now_value=now_fn(config))
    )
    loop_now = now_fn(config)
    _update_control_engine_status(
        shared_data,
        now_value=loop_now,
        set_alive=True,
        last_loop_start=loop_now,
    )

    refresh_fn()
    _update_control_engine_status(
        shared_data,
        now_value=now_fn(config),
        set_alive=True,
        last_observed_refresh=now_fn(config),
    )

    queue_obj = snapshot_locked(shared_data, lambda data: data.get("control_command_queue"))
    if queue_obj is None:
        _update_control_engine_status(shared_data, now_value=now_fn(config), set_alive=True, last_loop_end=now_fn(config))
        return None

    try:
        command = queue_obj.get_nowait()
    except queue.Empty:
        _update_control_engine_status(shared_data, now_value=now_fn(config), set_alive=True, last_loop_end=now_fn(config))
        return None

    command_id = run_command_with_lifecycle(
        shared_data,
        queue_obj=queue_obj,
        command=command,
        now_fn=lambda: now_fn(config),
        execute_command_fn=lambda queued_command: _execute_command(
            config,
            shared_data,
            queued_command,
            plant_ids=plant_ids,
            tz=tz,
            now_fn=now_fn,
            deps=deps,
        ),
        mark_command_running_fn=mark_command_running,
        mark_command_finished_fn=mark_command_finished,
        update_engine_status_fn=_update_control_engine_status,
        exception_log_prefix="ControlEngine",
    )

    refresh_fn()
    _update_control_engine_status(
        shared_data,
        now_value=now_fn(config),
        set_alive=True,
        last_observed_refresh=now_fn(config),
        last_loop_end=now_fn(config),
    )
    return command_id


def control_engine_agent(config, shared_data):
    """Process dashboard control commands and publish cached plant observed state."""
    logging.info("Control engine agent started.")
    plant_ids = tuple(config.get("PLANT_IDS", ("lib", "vrfb")))
    tz = get_config_tz(config)

    while not shared_data["shutdown_event"].is_set():
        loop_start = time.monotonic()
        try:
            _run_single_engine_cycle(config, shared_data, plant_ids=plant_ids, tz=tz)
        except Exception:
            logging.exception("ControlEngine: unexpected loop error.")
            error_now = now_tz(config)
            _update_control_engine_status(
                shared_data,
                now_value=error_now,
                set_alive=True,
                last_exception={"timestamp": error_now, "message": "unexpected loop error"},
                last_loop_end=error_now,
            )
        elapsed = time.monotonic() - loop_start
        time.sleep(max(0.0, CONTROL_ENGINE_LOOP_PERIOD_S - elapsed))

    _update_control_engine_status(shared_data, now_value=now_tz(config), set_alive=False, last_loop_end=now_tz(config))
    logging.info("Control engine agent stopped.")
