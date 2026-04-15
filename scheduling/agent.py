import logging
import time

import pandas as pd
from modbus.client import ModbusClient

from grid_map_runtime import snapshot_grid_map_runtime
from runtime.dispatch_write_runtime import publish_dispatch_write_status, set_dispatch_sending_enabled
import scheduling.manual_schedule_manager as msm
from runtime.contracts import resolve_modbus_endpoint
from modbus.setpoint_io import (
    build_setpoint_write_plan,
    read_setpoint_target_words_grouped,
    resolve_reactive_power_request,
    voltage_control_mode_supported,
    write_setpoint_plan_with_optional_trigger,
)
from scheduling.runtime import resolve_dispatch_bundle_from_sources
from runtime.shared_state import snapshot_locked
from time_utils import get_config_tz, now_tz


def scheduler_agent(config, shared_data):
    """Dispatch setpoints for LIB and VRFB in parallel using per-plant runtime gates."""
    logging.info("Scheduler agent started.")

    plant_ids = tuple(config.get("PLANT_IDS", ("lib", "vrfb")))
    tz = get_config_tz(config)

    raw_schedule_period_minutes = config.get("ISTENTORE_SCHEDULE_PERIOD_MINUTES", 15)
    try:
        schedule_period_minutes = float(raw_schedule_period_minutes)
        if schedule_period_minutes <= 0:
            raise ValueError("must be > 0")
    except (TypeError, ValueError):
        logging.warning(
            "Scheduler: Invalid ISTENTORE_SCHEDULE_PERIOD_MINUTES='%s'. Using 15 minutes.",
            raw_schedule_period_minutes,
        )
        schedule_period_minutes = 15.0
    api_validity_window = pd.Timedelta(minutes=schedule_period_minutes)

    clients = {plant_id: None for plant_id in plant_ids}
    endpoints = {plant_id: None for plant_id in plant_ids}
    previous_p = {plant_id: None for plant_id in plant_ids}
    previous_q = {plant_id: None for plant_id in plant_ids}
    previous_reactive_control_mode = {plant_id: None for plant_id in plant_ids}
    previous_api_stale = {plant_id: None for plant_id in plant_ids}
    retry_state_by_plant = {
        plant_id: {
            "pending_retry": False,
            "retry_signature": None,
            "next_retry_monotonic": None,
            "current_retry_delay_s": None,
        }
        for plant_id in plant_ids
    }
    last_manual_prune_day = None
    retry_initial_s = float(config.get("SCHEDULER_FAILED_WRITE_RETRY_INITIAL_S", 5.0) or 5.0)
    retry_max_s = max(retry_initial_s, float(config.get("SCHEDULER_FAILED_WRITE_RETRY_MAX_S", 20.0) or 20.0))
    retry_multiplier = max(1.0, float(config.get("SCHEDULER_FAILED_WRITE_RETRY_MULTIPLIER", 2.0) or 2.0))

    def clear_retry_state(plant_id):
        retry_state_by_plant[plant_id] = {
            "pending_retry": False,
            "retry_signature": None,
            "next_retry_monotonic": None,
            "current_retry_delay_s": None,
        }

    def schedule_retry(plant_id, signature, loop_mono):
        state = retry_state_by_plant[plant_id]
        if bool(state.get("pending_retry")) and state.get("retry_signature") == signature and state.get("current_retry_delay_s") is not None:
            delay_s = min(float(state["current_retry_delay_s"]) * retry_multiplier, retry_max_s)
        else:
            delay_s = retry_initial_s
        retry_state_by_plant[plant_id] = {
            "pending_retry": True,
            "retry_signature": signature,
            "next_retry_monotonic": float(loop_mono) + float(delay_s),
            "current_retry_delay_s": float(delay_s),
        }

    def build_retry_signature(write_plan, limit_result, voltage_setpoint_pu):
        write_quantity_mode = str((write_plan or {}).get("write_quantity_mode") or "pq")
        reactive_control_mode = (write_plan or {}).get("reactive_control_mode")
        applied_p_kw = limit_result.get("applied_p_kw")
        applied_q_kvar = None if write_quantity_mode == "pv" else limit_result.get("applied_q_kvar")
        applied_v_pu = float(voltage_setpoint_pu) if write_quantity_mode == "pv" else None
        return (
            write_quantity_mode,
            None if reactive_control_mode is None else int(reactive_control_mode),
            None if applied_p_kw is None else round(float(applied_p_kw), 9),
            None if applied_q_kvar is None else round(float(applied_q_kvar), 9),
            None if applied_v_pu is None else round(float(applied_v_pu), 9),
        )

    def ensure_client(plant_id, transport_mode):
        endpoint = resolve_modbus_endpoint(config, plant_id, transport_mode)
        endpoint_key = (endpoint["host"], endpoint["port"])

        if endpoints.get(plant_id) != endpoint_key:
            old_client = clients.get(plant_id)
            if old_client is not None:
                try:
                    old_client.close()
                except Exception:
                    pass

            clients[plant_id] = ModbusClient(host=endpoint["host"], port=endpoint["port"])
            endpoints[plant_id] = endpoint_key
            previous_p[plant_id] = None
            previous_q[plant_id] = None
            previous_reactive_control_mode[plant_id] = None
            clear_retry_state(plant_id)
            logging.info(
                "Scheduler: %s endpoint -> %s:%s (%s mode)",
                plant_id.upper(),
                endpoint["host"],
                endpoint["port"],
                transport_mode,
            )

        return clients[plant_id], endpoint

    while not shared_data["shutdown_event"].is_set():
        loop_start = time.monotonic()
        loop_now = now_tz(config)
        loop_mono = time.monotonic()

        current_day = loop_now.date()
        if current_day != last_manual_prune_day:
            window_start = loop_now.replace(hour=0, minute=0, second=0, microsecond=0)
            window_end = window_start + pd.Timedelta(days=2)
            with shared_data["lock"]:
                raw_series_map = dict(shared_data.get("manual_schedule_series_df_by_key", {}))
                for key in msm.MANUAL_SERIES_KEYS:
                    raw_series_map.setdefault(key, pd.DataFrame(columns=["setpoint"]))
                pruned_series_map = msm.prune_manual_series_map_to_window(raw_series_map, tz, window_start, window_end)
                shared_data["manual_schedule_series_df_by_key"] = pruned_series_map
                shared_data["manual_schedule_df_by_plant"] = msm.rebuild_manual_schedule_df_by_plant(
                    pruned_series_map,
                    timezone_name=config.get("TIMEZONE_NAME"),
                )
            last_manual_prune_day = current_day

        snapshot = snapshot_locked(
            shared_data,
            lambda data: {
                "transport_mode": data.get("transport_mode", "local"),
                "scheduler_running": dict(data.get("scheduler_running_by_plant", {})),
                "api_map": dict(data.get("api_schedule_df_by_plant", {})),
                "manual_series_map": dict(data.get("manual_schedule_series_df_by_key", {})),
                "manual_merge_enabled": dict(data.get("manual_schedule_merge_enabled_by_key", {})),
                "reactive_mode_by_plant": dict(data.get("reactive_control_mode_by_plant", {})),
            },
        )
        transport_mode = snapshot["transport_mode"]
        scheduler_running = snapshot["scheduler_running"]
        api_map = snapshot["api_map"]
        manual_series_map = snapshot["manual_series_map"]
        manual_merge_enabled = snapshot["manual_merge_enabled"]
        reactive_mode_by_plant = snapshot["reactive_mode_by_plant"]
        grid_map_runtime = snapshot_grid_map_runtime(shared_data)

        for plant_id in plant_ids:
            try:
                client, endpoint = ensure_client(plant_id, transport_mode)
                if client is None:
                    continue

                if not client.is_open:
                    if not client.open():
                        logging.warning("Scheduler: could not connect to %s plant endpoint.", plant_id.upper())
                        continue

                is_running = bool(scheduler_running.get(plant_id, False))
                set_dispatch_sending_enabled(shared_data, plant_id, is_running)
                if not is_running:
                    previous_p[plant_id] = None
                    previous_q[plant_id] = None
                    previous_reactive_control_mode[plant_id] = None
                    previous_api_stale[plant_id] = None
                    clear_retry_state(plant_id)
                    continue

                api_schedule_df = api_map.get(plant_id)
                p_key, q_key, v_key = msm.manual_series_keys_for_plant(plant_id, include_voltage=True)
                selected_reactive_mode = int(reactive_mode_by_plant.get(plant_id, 1) or 1)
                if selected_reactive_mode not in {1, 3}:
                    selected_reactive_mode = 1
                dispatch_bundle = resolve_dispatch_bundle_from_sources(
                    api_schedule_df,
                    manual_series_map.get(p_key),
                    manual_series_map.get(q_key),
                    manual_series_map.get(v_key),
                    loop_now,
                    tz,
                    manual_p_enabled=bool(manual_merge_enabled.get(p_key, False)),
                    manual_q_enabled=bool(manual_merge_enabled.get(q_key, False)),
                    manual_v_enabled=bool(manual_merge_enabled.get(v_key, False)),
                    selected_reactive_control_mode=selected_reactive_mode,
                    source="api",
                    api_validity_window=api_validity_window,
                    grid_map_runtime=grid_map_runtime,
                    digital_twin_voltage_enabled=voltage_control_mode_supported(endpoint),
                )
                requested_p_setpoint = float(dispatch_bundle["p_kw"])
                requested_q_setpoint = float(dispatch_bundle["q_kvar"])
                resolved_voltage_setpoint_pu = float(dispatch_bundle["voltage_setpoint_pu"])
                voltage_mode_active = bool(dispatch_bundle["voltage_mode_active"])
                is_stale = dispatch_bundle["api_is_stale"]
                if previous_api_stale[plant_id] != bool(is_stale):
                    if is_stale:
                        if api_schedule_df is None or api_schedule_df.empty:
                            logging.warning("Scheduler: %s API schedule unavailable -> base dispatch zero.", plant_id.upper())
                        else:
                            logging.warning("Scheduler: %s API setpoint stale -> base dispatch zero.", plant_id.upper())
                    else:
                        logging.info("Scheduler: %s API setpoint fresh again.", plant_id.upper())
                previous_api_stale[plant_id] = bool(is_stale)

                manual_p_applied = bool(manual_merge_enabled.get(p_key, False))
                manual_q_applied = bool(manual_merge_enabled.get(q_key, False))
                manual_v_applied = bool(manual_merge_enabled.get(v_key, False))

                p_write_ok = None
                q_write_ok = None
                reactive_control_mode_write_ok = None
                trigger_write_ok = None
                attempted_any = False

                reactive_dispatch = resolve_reactive_power_request(
                    client,
                    endpoint,
                    requested_q_kvar=requested_q_setpoint,
                    voltage_mode_active=voltage_mode_active,
                    voltage_setpoint_pu=resolved_voltage_setpoint_pu,
                )
                if not bool(reactive_dispatch.get("ok")):
                    attempted_any = True
                    publish_dispatch_write_status(
                        shared_data,
                        plant_id,
                        sending_enabled=True,
                        attempted_at=loop_now,
                        p_kw=requested_p_setpoint,
                        q_kvar=None,
                        source="scheduler",
                        status="failed",
                        error=str(reactive_dispatch.get("error") or "reactive_dispatch_failed"),
                        scheduler_context={
                            "api_stale": bool(is_stale),
                            "manual_p_applied": bool(manual_p_applied),
                            "manual_q_applied": bool(manual_q_applied),
                            "manual_v_applied": bool(manual_v_applied),
                            "selected_reactive_control_mode": int(selected_reactive_mode),
                            "reactive_control_mode": reactive_dispatch.get("reactive_control_mode"),
                            "voltage_mode_active": bool(voltage_mode_active),
                            "voltage_setpoint_pu": resolved_voltage_setpoint_pu,
                            "measured_v_poi_kv": reactive_dispatch.get("measured_v_poi_kv"),
                            "measured_v_poi_pu": reactive_dispatch.get("measured_v_poi_pu"),
                        },
                    )
                    logging.warning(
                        "Scheduler: %s reactive dispatch resolution failed (mode=%s error=%s).",
                        plant_id.upper(),
                        "voltage" if voltage_mode_active else "q",
                        reactive_dispatch.get("error"),
                    )
                    clear_retry_state(plant_id)
                    continue

                requested_q_for_plan = reactive_dispatch.get("requested_q_kvar", requested_q_setpoint)
                reactive_control_mode = reactive_dispatch.get("reactive_control_mode")
                write_plan = build_setpoint_write_plan(
                    endpoint,
                    requested_p_setpoint,
                    requested_q_for_plan,
                    reactive_control_mode=reactive_control_mode,
                    voltage_setpoint_pu=resolved_voltage_setpoint_pu,
                )
                limit_result = dict((write_plan or {}).get("limit_result") or {})
                applied_p_setpoint = float(limit_result.get("applied_p_kw", requested_p_setpoint))
                applied_q_raw = limit_result.get("applied_q_kvar", requested_q_for_plan)
                applied_q_setpoint = None if applied_q_raw is None else float(applied_q_raw)
                retry_signature = build_retry_signature(write_plan, limit_result, resolved_voltage_setpoint_pu)
                retry_state = dict(retry_state_by_plant.get(plant_id, {}) or {})
                if retry_state.get("retry_signature") != retry_signature:
                    clear_retry_state(plant_id)
                    retry_state = dict(retry_state_by_plant.get(plant_id, {}) or {})

                if bool(retry_state.get("pending_retry")) and retry_state.get("retry_signature") == retry_signature:
                    next_retry_monotonic = retry_state.get("next_retry_monotonic")
                    if next_retry_monotonic is not None and float(loop_mono) < float(next_retry_monotonic):
                        continue

                control_mode_targets = list(write_plan.get("control_mode_targets") or [])
                p_targets = list(write_plan.get("p_targets") or [])
                q_targets = list(write_plan.get("q_targets") or [])
                voltage_targets = list(write_plan.get("voltage_targets") or [])
                all_readback_targets = control_mode_targets + p_targets + q_targets + voltage_targets
                retry_due = bool(retry_state.get("pending_retry")) and retry_state.get("retry_signature") == retry_signature
                try:
                    all_actual_words = read_setpoint_target_words_grouped(client, endpoint, all_readback_targets)
                except Exception as exc:
                    logging.warning("Scheduler: %s grouped setpoint readback failed: %s", plant_id.upper(), exc)
                    all_actual_words = {str(target["point_name"]): None for target in all_readback_targets}

                reactive_control_mode_actual_words = {
                    str(target["point_name"]): all_actual_words.get(str(target["point_name"]))
                    for target in control_mode_targets
                }
                p_actual_words = {
                    str(target["point_name"]): all_actual_words.get(str(target["point_name"]))
                    for target in p_targets
                }
                q_actual_words = {
                    str(target["point_name"]): all_actual_words.get(str(target["point_name"]))
                    for target in q_targets
                }
                v_actual_words = {
                    str(target["point_name"]): all_actual_words.get(str(target["point_name"]))
                    for target in voltage_targets
                }

                reactive_control_mode_readback_ok = all(
                    reactive_control_mode_actual_words.get(str(target["point_name"])) is not None
                    for target in control_mode_targets
                )
                p_readback_ok = all(p_actual_words.get(str(target["point_name"])) is not None for target in p_targets)
                q_readback_ok = all(q_actual_words.get(str(target["point_name"])) is not None for target in q_targets)
                v_readback_ok = all(v_actual_words.get(str(target["point_name"])) is not None for target in voltage_targets)

                reactive_control_mode_readback_mismatch = None
                if control_mode_targets and reactive_control_mode_readback_ok:
                    reactive_control_mode_readback_mismatch = any(
                        list(reactive_control_mode_actual_words.get(str(target["point_name"])) or []) != list(target["target_words"])
                        for target in control_mode_targets
                    )
                p_readback_mismatch = None
                if p_readback_ok:
                    p_readback_mismatch = any(
                        list(p_actual_words.get(str(target["point_name"])) or []) != list(target["target_words"])
                        for target in p_targets
                    )
                q_readback_mismatch = None
                if q_readback_ok:
                    q_readback_mismatch = any(
                        list(q_actual_words.get(str(target["point_name"])) or []) != list(target["target_words"])
                        for target in q_targets
                    )
                v_readback_mismatch = None
                if v_readback_ok:
                    v_readback_mismatch = any(
                        list(v_actual_words.get(str(target["point_name"])) or []) != list(target["target_words"])
                        for target in voltage_targets
                    )

                if not p_readback_ok:
                    p_compare_source = "cache_fallback"
                    p_should_write = previous_p[plant_id] != applied_p_setpoint
                else:
                    p_compare_source = "readback"
                    p_should_write = bool(p_readback_mismatch)
                if q_targets:
                    if not q_readback_ok:
                        q_compare_source = "cache_fallback"
                        q_should_write = previous_q[plant_id] != applied_q_setpoint
                    else:
                        q_compare_source = "readback"
                        q_should_write = bool(q_readback_mismatch)
                else:
                    q_compare_source = "skipped_voltage_mode"
                    q_should_write = False

                if voltage_targets:
                    if not v_readback_ok:
                        v_compare_source = "cache_fallback"
                        v_should_write = True
                    else:
                        v_compare_source = "readback"
                        v_should_write = bool(v_readback_mismatch)
                else:
                    v_compare_source = "not_requested"
                    v_should_write = False

                if control_mode_targets:
                    if not reactive_control_mode_readback_ok:
                        reactive_control_mode_compare_source = "cache_fallback"
                        reactive_control_mode_should_write = (
                            previous_reactive_control_mode[plant_id] != reactive_control_mode
                        )
                    else:
                        reactive_control_mode_compare_source = "readback"
                        reactive_control_mode_should_write = bool(reactive_control_mode_readback_mismatch)
                else:
                    reactive_control_mode_compare_source = "not_configured"
                    reactive_control_mode_should_write = False

                readback_targets_ok = (
                    reactive_control_mode_readback_ok
                    and p_readback_ok
                    and q_readback_ok
                    and v_readback_ok
                )
                readback_targets_mismatch = any(
                    mismatch is True
                    for mismatch in (
                        reactive_control_mode_readback_mismatch,
                        p_readback_mismatch,
                        q_readback_mismatch,
                        v_readback_mismatch,
                    )
                )
                if retry_due and readback_targets_ok and not readback_targets_mismatch:
                    clear_retry_state(plant_id)
                    retry_due = False

                should_apply = bool(
                    retry_due
                    or reactive_control_mode_should_write
                    or p_should_write
                    or q_should_write
                    or v_should_write
                )
                if should_apply:
                    attempted_any = True
                    if bool(limit_result.get("any_clamped")):
                        if write_plan["write_quantity_mode"] == "pv":
                            logging.warning(
                                "Scheduler: %s setpoints clamped before write (requested P=%.3f, applied P=%.3f, V=%.3f pu).",
                                plant_id.upper(),
                                float(limit_result.get("requested_p_kw", requested_p_setpoint)),
                                applied_p_setpoint,
                                resolved_voltage_setpoint_pu,
                            )
                        else:
                            logging.warning(
                                "Scheduler: %s setpoints clamped before write (requested P=%.3f Q=%.3f, applied P=%.3f Q=%.3f).",
                                plant_id.upper(),
                                float(limit_result.get("requested_p_kw", requested_p_setpoint)),
                                float(limit_result.get("requested_q_kvar", requested_q_for_plan)),
                                applied_p_setpoint,
                                applied_q_setpoint,
                            )
                    apply_result = write_setpoint_plan_with_optional_trigger(client, endpoint, write_plan)
                    reactive_control_mode_write_ok = bool((apply_result.get("control_mode_result") or {}).get("ok"))
                    p_write_ok = bool((apply_result.get("p_result") or {}).get("ok"))
                    q_write_ok = bool((apply_result.get("q_result") or {}).get("ok"))
                    v_write_ok = bool((apply_result.get("voltage_result") or {}).get("ok"))
                    trigger_result = dict(apply_result.get("trigger_result") or {})
                    trigger_write_ok = str(trigger_result.get("state")) in {"ok", "skipped"}
                    if bool(apply_result.get("ok")):
                        previous_p[plant_id] = applied_p_setpoint
                        previous_q[plant_id] = applied_q_setpoint
                        previous_reactive_control_mode[plant_id] = reactive_control_mode
                        clear_retry_state(plant_id)

                if attempted_any:
                    attempted_results = [
                        value
                        for value in (reactive_control_mode_write_ok, p_write_ok, q_write_ok, v_write_ok)
                        if value is not None
                    ]
                    ok_count = sum(1 for value in attempted_results if value is True)
                    fail_count = sum(1 for value in attempted_results if value is False)
                    if fail_count == 0 and trigger_write_ok is True:
                        attempt_status = "ok"
                        error_text = None
                    elif fail_count == 0 and trigger_write_ok is False:
                        attempt_status = "failed"
                        error_text = "setpoint_trigger_failed"
                    elif ok_count > 0:
                        attempt_status = "partial"
                        error_text = "setpoint_write_partial_failure"
                    else:
                        attempt_status = "failed"
                        error_text = "setpoint_write_failed"
                    publish_dispatch_write_status(
                        shared_data,
                        plant_id,
                        sending_enabled=True,
                        attempted_at=loop_now,
                        p_kw=applied_p_setpoint,
                        q_kvar=None if write_plan["write_quantity_mode"] == "pv" else applied_q_setpoint,
                        source="scheduler",
                        status=attempt_status,
                        error=error_text,
                        scheduler_context={
                            "api_stale": bool(is_stale),
                            "manual_p_applied": bool(manual_p_applied),
                            "manual_q_applied": bool(manual_q_applied),
                            "manual_v_applied": bool(manual_v_applied),
                            "readback_compare_mode": "register_exact",
                            "setpoint_mode": write_plan["mode"],
                            "write_quantity_mode": write_plan["write_quantity_mode"],
                            "reactive_control_mode": reactive_control_mode,
                            "reactive_control_mode_compare_source": reactive_control_mode_compare_source,
                            "reactive_control_mode_readback_ok": bool(reactive_control_mode_readback_ok),
                            "reactive_control_mode_readback_mismatch": reactive_control_mode_readback_mismatch,
                            "voltage_mode_active": bool(voltage_mode_active),
                            "voltage_setpoint_pu": resolved_voltage_setpoint_pu,
                            "measured_v_poi_kv": reactive_dispatch.get("measured_v_poi_kv"),
                            "measured_v_poi_pu": reactive_dispatch.get("measured_v_poi_pu"),
                            "p_compare_source": p_compare_source,
                            "q_compare_source": q_compare_source,
                            "v_compare_source": v_compare_source,
                            "p_readback_ok": bool(p_readback_ok),
                            "q_readback_ok": bool(q_readback_ok),
                            "v_readback_ok": bool(v_readback_ok),
                            "p_readback_mismatch": p_readback_mismatch,
                            "q_readback_mismatch": q_readback_mismatch,
                            "v_readback_mismatch": v_readback_mismatch,
                            "requested_p_kw": float(limit_result.get("requested_p_kw", requested_p_setpoint)),
                            "requested_q_kvar": None if limit_result.get("requested_q_kvar") is None else float(limit_result.get("requested_q_kvar", requested_q_for_plan)),
                            "applied_p_kw": applied_p_setpoint,
                            "applied_q_kvar": None if applied_q_setpoint is None else applied_q_setpoint,
                            "p_clamped": bool(limit_result.get("p_clamped", False)),
                            "q_clamped": bool(limit_result.get("q_clamped", False)),
                            "any_clamped": bool(limit_result.get("any_clamped", False)),
                        },
                    )
                    if fail_count > 0:
                        if write_plan["write_quantity_mode"] == "pv":
                            logging.warning(
                                "Scheduler: %s setpoint write %s (P=%s ok=%s, V=%s pu ok=%s, trigger_ok=%s).",
                                plant_id.upper(),
                                attempt_status,
                                f"{applied_p_setpoint:.3f}",
                                p_write_ok,
                                f"{resolved_voltage_setpoint_pu:.3f}",
                                v_write_ok,
                                trigger_write_ok,
                            )
                        else:
                            logging.warning(
                                "Scheduler: %s setpoint write %s (P=%s ok=%s, Q=%s ok=%s, trigger_ok=%s).",
                                plant_id.upper(),
                                attempt_status,
                                f"{applied_p_setpoint:.3f}",
                                p_write_ok,
                                f"{applied_q_setpoint:.3f}",
                                q_write_ok,
                                trigger_write_ok,
                            )
                    elif trigger_write_ok is False:
                        if write_plan["write_quantity_mode"] == "pv":
                            logging.warning(
                                "Scheduler: %s trigger pulse failed after setpoint write (P=%.3f V=%.3f pu).",
                                plant_id.upper(),
                                applied_p_setpoint,
                                resolved_voltage_setpoint_pu,
                            )
                        else:
                            logging.warning(
                                "Scheduler: %s trigger pulse failed after setpoint write (P=%.3f Q=%.3f).",
                                plant_id.upper(),
                                applied_p_setpoint,
                                applied_q_setpoint,
                            )
                    if attempt_status in {"failed", "partial"}:
                        schedule_retry(plant_id, retry_signature, loop_mono)
                    elif attempt_status == "ok":
                        clear_retry_state(plant_id)

            except Exception as exc:
                logging.error("Scheduler error for %s: %s", plant_id.upper(), exc)

        elapsed = time.monotonic() - loop_start
        time.sleep(max(0.0, float(config.get("SCHEDULER_PERIOD_S", 1)) - elapsed))

    for client in clients.values():
        try:
            if client is not None:
                client.close()
        except Exception:
            pass

    logging.info("Scheduler agent stopped.")
