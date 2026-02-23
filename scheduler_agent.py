import logging
import time

import pandas as pd
from pyModbusTCP.client import ModbusClient

from modbus_codec import write_point_internal
from runtime_contracts import resolve_modbus_endpoint
from schedule_runtime import resolve_schedule_setpoint
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

        snapshot = snapshot_locked(
            shared_data,
            lambda data: {
                "transport_mode": data.get("transport_mode", "local"),
                "active_source": data.get("active_schedule_source", "manual"),
                "scheduler_running": dict(data.get("scheduler_running_by_plant", {})),
                "manual_map": dict(data.get("manual_schedule_df_by_plant", {})),
                "api_map": dict(data.get("api_schedule_df_by_plant", {})),
            },
        )
        transport_mode = snapshot["transport_mode"]
        active_source = snapshot["active_source"]
        scheduler_running = snapshot["scheduler_running"]
        manual_map = snapshot["manual_map"]
        api_map = snapshot["api_map"]

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
                if not is_running:
                    previous_p[plant_id] = None
                    previous_q[plant_id] = None
                    previous_api_stale[plant_id] = None
                    continue

                schedule_df = api_map.get(plant_id) if active_source == "api" else manual_map.get(plant_id)

                p_setpoint, q_setpoint, is_stale = resolve_schedule_setpoint(
                    schedule_df,
                    now_tz(config),
                    tz,
                    source=active_source,
                    api_validity_window=api_validity_window,
                )

                if active_source == "api":
                    if previous_api_stale[plant_id] != bool(is_stale):
                        if is_stale:
                            if schedule_df is None or schedule_df.empty:
                                logging.warning("Scheduler: %s API schedule unavailable -> zero dispatch.", plant_id.upper())
                            else:
                                logging.warning("Scheduler: %s API setpoint stale -> zero dispatch.", plant_id.upper())
                        else:
                            logging.info("Scheduler: %s API setpoint fresh again.", plant_id.upper())
                    previous_api_stale[plant_id] = bool(is_stale)
                else:
                    previous_api_stale[plant_id] = None

                if previous_p[plant_id] != p_setpoint:
                    write_point_internal(client, endpoint, "p_setpoint", p_setpoint)
                    previous_p[plant_id] = p_setpoint

                if previous_q[plant_id] != q_setpoint:
                    write_point_internal(client, endpoint, "q_setpoint", q_setpoint)
                    previous_q[plant_id] = q_setpoint

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
