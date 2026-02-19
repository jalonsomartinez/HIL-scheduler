import logging
import time

import pandas as pd
from pyModbusTCP.client import ModbusClient

from time_utils import get_config_tz, normalize_schedule_index, now_tz
from utils import int_to_uint16, kw_to_hw


def scheduler_agent(config, shared_data):
    """Dispatch setpoints for LIB and VRFB in parallel using per-plant runtime gates."""
    logging.info("Scheduler agent started.")

    plant_ids = tuple(config.get("PLANT_IDS", ("lib", "vrfb")))
    plants_cfg = config.get("PLANTS", {})
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

    def get_endpoint(plant_id, transport_mode):
        plant_cfg = plants_cfg.get(plant_id, {})
        endpoint = (plant_cfg.get("modbus", {}) or {}).get(transport_mode, {})
        registers = endpoint.get("registers", {})
        return {
            "host": endpoint.get("host", "localhost"),
            "port": int(endpoint.get("port", 5020)),
            "p_setpoint_reg": int(registers.get("p_setpoint_in", 86)),
            "q_setpoint_reg": int(registers.get("q_setpoint_in", 88)),
        }

    def ensure_client(plant_id, transport_mode):
        endpoint = get_endpoint(plant_id, transport_mode)
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

        with shared_data["lock"]:
            transport_mode = shared_data.get("transport_mode", "local")
            active_source = shared_data.get("active_schedule_source", "manual")
            scheduler_running = dict(shared_data.get("scheduler_running_by_plant", {}))
            manual_map = dict(shared_data.get("manual_schedule_df_by_plant", {}))
            api_map = dict(shared_data.get("api_schedule_df_by_plant", {}))

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

                p_setpoint = 0.0
                q_setpoint = 0.0

                if schedule_df is not None and not schedule_df.empty:
                    now_value = now_tz(config)
                    schedule_df = normalize_schedule_index(schedule_df, tz)
                    current_row = schedule_df.asof(now_value)

                    if current_row is not None and not current_row.empty:
                        p_setpoint = float(current_row.get("power_setpoint_kw", 0.0) or 0.0)
                        q_setpoint = float(current_row.get("reactive_power_setpoint_kvar", 0.0) or 0.0)

                        if active_source == "api":
                            row_ts = schedule_df.index.asof(now_value)
                            is_stale = pd.isna(row_ts) or (
                                pd.Timestamp(now_value) - pd.Timestamp(row_ts) > api_validity_window
                            )
                            if is_stale:
                                p_setpoint = 0.0
                                q_setpoint = 0.0
                            if previous_api_stale[plant_id] != is_stale:
                                if is_stale:
                                    logging.warning("Scheduler: %s API setpoint stale -> zero dispatch.", plant_id.upper())
                                else:
                                    logging.info("Scheduler: %s API setpoint fresh again.", plant_id.upper())
                            previous_api_stale[plant_id] = is_stale
                        else:
                            previous_api_stale[plant_id] = None

                        if pd.isna(p_setpoint) or pd.isna(q_setpoint):
                            p_setpoint = 0.0
                            q_setpoint = 0.0
                elif active_source == "api":
                    if previous_api_stale[plant_id] is not True:
                        logging.warning("Scheduler: %s API schedule unavailable -> zero dispatch.", plant_id.upper())
                    previous_api_stale[plant_id] = True

                if previous_p[plant_id] != p_setpoint:
                    client.write_single_register(endpoint["p_setpoint_reg"], int_to_uint16(kw_to_hw(p_setpoint)))
                    previous_p[plant_id] = p_setpoint

                if previous_q[plant_id] != q_setpoint:
                    client.write_single_register(endpoint["q_setpoint_reg"], int_to_uint16(kw_to_hw(q_setpoint)))
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
