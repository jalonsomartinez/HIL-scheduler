import logging
import time

import pandas as pd
from pyModbusTCP.client import ModbusClient

from dispatch_write_runtime import publish_dispatch_write_status, set_dispatch_sending_enabled
import manual_schedule_manager as msm
from modbus_codec import encode_point_internal_words, read_point_words, write_point_internal
from runtime_contracts import resolve_modbus_endpoint
from schedule_runtime import resolve_schedule_setpoint, resolve_series_setpoint_asof, split_manual_override_series
from shared_state import snapshot_locked
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
                    continue

                api_schedule_df = api_map.get(plant_id)
                p_setpoint, q_setpoint, is_stale = resolve_schedule_setpoint(
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
                    p_setpoint = manual_p_value
                    manual_p_applied = True
                else:
                    manual_p_applied = False
                if (
                    bool(manual_merge_enabled.get(q_key, False))
                    and manual_q_has
                    and (manual_q_end_time is None or pd.Timestamp(loop_now) < pd.Timestamp(manual_q_end_time))
                ):
                    q_setpoint = manual_q_value
                    manual_q_applied = True
                else:
                    manual_q_applied = False

                p_write_ok = None
                q_write_ok = None
                attempted_any = False

                p_target_words = encode_point_internal_words(endpoint, "p_setpoint", p_setpoint)
                q_target_words = encode_point_internal_words(endpoint, "q_setpoint", q_setpoint)

                try:
                    p_actual_words = read_point_words(client, endpoint, "p_setpoint")
                except Exception as exc:
                    logging.warning("Scheduler: %s p_setpoint readback failed: %s", plant_id.upper(), exc)
                    p_actual_words = None
                try:
                    q_actual_words = read_point_words(client, endpoint, "q_setpoint")
                except Exception as exc:
                    logging.warning("Scheduler: %s q_setpoint readback failed: %s", plant_id.upper(), exc)
                    q_actual_words = None

                p_readback_mismatch = None if p_actual_words is None else (list(p_actual_words) != list(p_target_words))
                q_readback_mismatch = None if q_actual_words is None else (list(q_actual_words) != list(q_target_words))

                if p_actual_words is None:
                    p_compare_source = "cache_fallback"
                    p_should_write = previous_p[plant_id] != p_setpoint
                else:
                    p_compare_source = "readback"
                    p_should_write = bool(p_readback_mismatch)
                if q_actual_words is None:
                    q_compare_source = "cache_fallback"
                    q_should_write = previous_q[plant_id] != q_setpoint
                else:
                    q_compare_source = "readback"
                    q_should_write = bool(q_readback_mismatch)

                if p_should_write:
                    attempted_any = True
                    p_write_ok = bool(write_point_internal(client, endpoint, "p_setpoint", p_setpoint))
                    if p_write_ok:
                        previous_p[plant_id] = p_setpoint

                if q_should_write:
                    attempted_any = True
                    q_write_ok = bool(write_point_internal(client, endpoint, "q_setpoint", q_setpoint))
                    if q_write_ok:
                        previous_q[plant_id] = q_setpoint

                if attempted_any:
                    attempted_results = [value for value in (p_write_ok, q_write_ok) if value is not None]
                    ok_count = sum(1 for value in attempted_results if value is True)
                    fail_count = sum(1 for value in attempted_results if value is False)
                    if fail_count == 0:
                        attempt_status = "ok"
                        error_text = None
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
                        p_kw=p_setpoint,
                        q_kvar=q_setpoint,
                        source="scheduler",
                        status=attempt_status,
                        error=error_text,
                        scheduler_context={
                            "api_stale": bool(is_stale),
                            "manual_p_applied": bool(manual_p_applied),
                            "manual_q_applied": bool(manual_q_applied),
                            "readback_compare_mode": "register_exact",
                            "p_compare_source": p_compare_source,
                            "q_compare_source": q_compare_source,
                            "p_readback_ok": bool(p_actual_words is not None),
                            "q_readback_ok": bool(q_actual_words is not None),
                            "p_readback_mismatch": p_readback_mismatch,
                            "q_readback_mismatch": q_readback_mismatch,
                        },
                    )
                    if fail_count > 0:
                        logging.warning(
                            "Scheduler: %s setpoint write %s (P=%s ok=%s, Q=%s ok=%s).",
                            plant_id.upper(),
                            attempt_status,
                            f"{p_setpoint:.3f}",
                            p_write_ok,
                            f"{q_setpoint:.3f}",
                            q_write_ok,
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
