import logging
import time

import pandas as pd
from modbus.client import ModbusClient

from runtime.dispatch_write_runtime import publish_dispatch_write_status, set_dispatch_sending_enabled
import scheduling.manual_schedule_manager as msm
from runtime.contracts import resolve_modbus_endpoint
from modbus.setpoint_io import build_setpoint_write_plan, read_setpoint_target_words, write_setpoint_plan_with_optional_trigger
from scheduling.runtime import resolve_schedule_setpoint, resolve_series_setpoint_asof, split_manual_override_series
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
    previous_api_stale = {plant_id: None for plant_id in plant_ids}
    force_setpoint_retry = {plant_id: False for plant_id in plant_ids}
    last_manual_prune_day = None

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
            logging.info(
                "Scheduler: %s endpoint -> %s:%s (%s mode)",
                plant_id.upper(),
                endpoint["host"],
                endpoint["port"],
                transport_mode,
            )

        return clients[plant_id], endpoint

    while not shared_data["shutdown_event"].is_set():
        loop_start = time.time()
        loop_now = now_tz(config)

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
            },
        )
        transport_mode = snapshot["transport_mode"]
        scheduler_running = snapshot["scheduler_running"]
        api_map = snapshot["api_map"]
        manual_series_map = snapshot["manual_series_map"]
        manual_merge_enabled = snapshot["manual_merge_enabled"]

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
                    previous_api_stale[plant_id] = None
                    force_setpoint_retry[plant_id] = False
                    continue

                api_schedule_df = api_map.get(plant_id)
                requested_p_setpoint, requested_q_setpoint, is_stale = resolve_schedule_setpoint(
                    api_schedule_df,
                    loop_now,
                    tz,
                    source="api",
                    api_validity_window=api_validity_window,
                )
                if previous_api_stale[plant_id] != bool(is_stale):
                    if is_stale:
                        if api_schedule_df is None or api_schedule_df.empty:
                            logging.warning("Scheduler: %s API schedule unavailable -> base dispatch zero.", plant_id.upper())
                        else:
                            logging.warning("Scheduler: %s API setpoint stale -> base dispatch zero.", plant_id.upper())
                    else:
                        logging.info("Scheduler: %s API setpoint fresh again.", plant_id.upper())
                previous_api_stale[plant_id] = bool(is_stale)

                p_key, q_key = msm.manual_series_keys_for_plant(plant_id)
                manual_p_value, manual_p_has = resolve_series_setpoint_asof(manual_series_map.get(p_key), loop_now, tz)
                manual_q_value, manual_q_has = resolve_series_setpoint_asof(manual_series_map.get(q_key), loop_now, tz)
                manual_p_end_time = split_manual_override_series(manual_series_map.get(p_key), tz).get("end_ts")
                manual_q_end_time = split_manual_override_series(manual_series_map.get(q_key), tz).get("end_ts")

                if (
                    bool(manual_merge_enabled.get(p_key, False))
                    and manual_p_has
                    and (manual_p_end_time is None or pd.Timestamp(loop_now) < pd.Timestamp(manual_p_end_time))
                ):
                    requested_p_setpoint = manual_p_value
                    manual_p_applied = True
                else:
                    manual_p_applied = False
                if (
                    bool(manual_merge_enabled.get(q_key, False))
                    and manual_q_has
                    and (manual_q_end_time is None or pd.Timestamp(loop_now) < pd.Timestamp(manual_q_end_time))
                ):
                    requested_q_setpoint = manual_q_value
                    manual_q_applied = True
                else:
                    manual_q_applied = False

                p_write_ok = None
                q_write_ok = None
                trigger_write_ok = None
                attempted_any = False

                write_plan = build_setpoint_write_plan(endpoint, requested_p_setpoint, requested_q_setpoint)
                limit_result = dict((write_plan or {}).get("limit_result") or {})
                applied_p_setpoint = float(limit_result.get("applied_p_kw", requested_p_setpoint))
                applied_q_setpoint = float(limit_result.get("applied_q_kvar", requested_q_setpoint))

                try:
                    p_actual_words = read_setpoint_target_words(client, endpoint, write_plan["p_targets"])
                except Exception as exc:
                    logging.warning("Scheduler: %s P setpoint readback failed: %s", plant_id.upper(), exc)
                    p_actual_words = {str(target["point_name"]): None for target in write_plan["p_targets"]}
                try:
                    q_actual_words = read_setpoint_target_words(client, endpoint, write_plan["q_targets"])
                except Exception as exc:
                    logging.warning("Scheduler: %s Q setpoint readback failed: %s", plant_id.upper(), exc)
                    q_actual_words = {str(target["point_name"]): None for target in write_plan["q_targets"]}

                p_readback_ok = all(p_actual_words.get(str(target["point_name"])) is not None for target in write_plan["p_targets"])
                q_readback_ok = all(q_actual_words.get(str(target["point_name"])) is not None for target in write_plan["q_targets"])

                p_readback_mismatch = None
                if p_readback_ok:
                    p_readback_mismatch = any(
                        list(p_actual_words.get(str(target["point_name"])) or []) != list(target["target_words"])
                        for target in write_plan["p_targets"]
                    )
                q_readback_mismatch = None
                if q_readback_ok:
                    q_readback_mismatch = any(
                        list(q_actual_words.get(str(target["point_name"])) or []) != list(target["target_words"])
                        for target in write_plan["q_targets"]
                    )

                if not p_readback_ok:
                    p_compare_source = "cache_fallback"
                    p_should_write = previous_p[plant_id] != applied_p_setpoint
                else:
                    p_compare_source = "readback"
                    p_should_write = bool(p_readback_mismatch)
                if not q_readback_ok:
                    q_compare_source = "cache_fallback"
                    q_should_write = previous_q[plant_id] != applied_q_setpoint
                else:
                    q_compare_source = "readback"
                    q_should_write = bool(q_readback_mismatch)

                should_apply = bool(force_setpoint_retry[plant_id] or p_should_write or q_should_write)
                if should_apply:
                    attempted_any = True
                    if bool(limit_result.get("any_clamped")):
                        logging.warning(
                            "Scheduler: %s setpoints clamped before write (requested P=%.3f Q=%.3f, applied P=%.3f Q=%.3f).",
                            plant_id.upper(),
                            float(limit_result.get("requested_p_kw", requested_p_setpoint)),
                            float(limit_result.get("requested_q_kvar", requested_q_setpoint)),
                            applied_p_setpoint,
                            applied_q_setpoint,
                        )
                    apply_result = write_setpoint_plan_with_optional_trigger(client, endpoint, write_plan)
                    p_write_ok = bool((apply_result.get("p_result") or {}).get("ok"))
                    q_write_ok = bool((apply_result.get("q_result") or {}).get("ok"))
                    trigger_result = dict(apply_result.get("trigger_result") or {})
                    trigger_write_ok = str(trigger_result.get("state")) in {"ok", "skipped"}
                    if bool(apply_result.get("ok")):
                        previous_p[plant_id] = applied_p_setpoint
                        previous_q[plant_id] = applied_q_setpoint
                        force_setpoint_retry[plant_id] = False
                    else:
                        force_setpoint_retry[plant_id] = True

                if attempted_any:
                    attempted_results = [value for value in (p_write_ok, q_write_ok) if value is not None]
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
                        q_kvar=applied_q_setpoint,
                        source="scheduler",
                        status=attempt_status,
                        error=error_text,
                        scheduler_context={
                            "api_stale": bool(is_stale),
                            "manual_p_applied": bool(manual_p_applied),
                            "manual_q_applied": bool(manual_q_applied),
                            "readback_compare_mode": "register_exact",
                            "setpoint_mode": write_plan["mode"],
                            "p_compare_source": p_compare_source,
                            "q_compare_source": q_compare_source,
                            "p_readback_ok": bool(p_readback_ok),
                            "q_readback_ok": bool(q_readback_ok),
                            "p_readback_mismatch": p_readback_mismatch,
                            "q_readback_mismatch": q_readback_mismatch,
                            "requested_p_kw": float(limit_result.get("requested_p_kw", requested_p_setpoint)),
                            "requested_q_kvar": float(limit_result.get("requested_q_kvar", requested_q_setpoint)),
                            "applied_p_kw": applied_p_setpoint,
                            "applied_q_kvar": applied_q_setpoint,
                            "p_clamped": bool(limit_result.get("p_clamped", False)),
                            "q_clamped": bool(limit_result.get("q_clamped", False)),
                            "any_clamped": bool(limit_result.get("any_clamped", False)),
                        },
                    )
                    if fail_count > 0:
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
                        logging.warning(
                            "Scheduler: %s trigger pulse failed after setpoint write (P=%.3f Q=%.3f).",
                            plant_id.upper(),
                            applied_p_setpoint,
                            applied_q_setpoint,
                        )

            except Exception as exc:
                logging.error("Scheduler error for %s: %s", plant_id.upper(), exc)

        elapsed = time.time() - loop_start
        time.sleep(max(0.0, float(config.get("SCHEDULER_PERIOD_S", 1)) - elapsed))

    for client in clients.values():
        try:
            if client is not None:
                client.close()
        except Exception:
            pass

    logging.info("Scheduler agent stopped.")
