import logging
import queue
import threading
import time

import pandas as pd

from runtime.api_runtime_state import default_api_connection_runtime
from runtime.dispatch_write_runtime import default_dispatch_write_status_by_plant
from runtime.engine_status_runtime import default_engine_status
import scheduling.manual_schedule_manager as msm
from config_loader import load_config
from control.engine_agent import control_engine_agent
from dashboard.agent import dashboard_agent
from data_fetcher_agent import data_fetcher_agent
from logger_config import setup_logging
from measurement.agent import measurement_agent
from plant_agent import plant_agent
from scheduling.agent import scheduler_agent
from settings.engine_agent import settings_engine_agent


def _empty_df_by_plant(plant_ids):
    return {plant_id: pd.DataFrame() for plant_id in plant_ids}


def _empty_manual_series_df_by_key():
    return msm.default_manual_series_map()

def _default_manual_merge_enabled_by_key():
    return msm.default_manual_merge_enabled_map(default_enabled=False)


def _default_measurement_post_status_by_plant(plant_ids):
    return {
        plant_id: {
            "posting_enabled": False,
            "last_success": None,
            "last_attempt": None,
            "last_error": None,
            "pending_queue_count": 0,
            "oldest_pending_age_s": None,
            "last_enqueue": None,
        }
        for plant_id in plant_ids
    }


def _default_local_emulator_soc_seed_request_by_plant(plant_ids):
    return {plant_id: None for plant_id in plant_ids}


def _default_local_emulator_soc_seed_result_by_plant(plant_ids):
    return {
        plant_id: {
            "request_id": None,
            "status": "idle",
            "soc_pu": None,
            "message": None,
        }
        for plant_id in plant_ids
    }


def _default_plant_observed_state_by_plant(plant_ids):
    return {
        plant_id: {
            "enable_state": None,
            "p_battery_kw": None,
            "q_battery_kvar": None,
            "last_attempt": None,
            "last_success": None,
            "error": None,
            "read_status": "unknown",
            "last_error": None,
            "consecutive_failures": 0,
            "stale": True,
        }
        for plant_id in plant_ids
    }


def _default_plant_operating_state_by_plant(plant_ids):
    return {plant_id: "unknown" for plant_id in plant_ids}


def _default_manual_series_runtime_state_by_key():
    series_map = _empty_manual_series_df_by_key()
    merge_map = _default_manual_merge_enabled_by_key()
    state_map = {}
    for key in msm.MANUAL_SERIES_KEYS:
        active = bool(merge_map.get(key, False))
        state_map[key] = {
            "state": "active" if active else "inactive",
            "desired_state": "active" if active else "inactive",
            "active": active,
            "applied_series_df": series_map.get(key, pd.DataFrame(columns=["setpoint"])),
            "last_command_id": None,
            "last_error": None,
            "last_updated": None,
            "last_success": None,
        }
    return state_map


def _default_api_connection_runtime():
    return default_api_connection_runtime()


def _default_posting_runtime(policy_enabled):
    terminal = "enabled" if bool(policy_enabled) else "disabled"
    return {
        "state": terminal,
        "policy_enabled": bool(policy_enabled),
        "desired_state": terminal,
        "last_command_id": None,
        "last_error": None,
        "last_updated": None,
        "last_success": None,
    }


def build_initial_shared_data(config):
    """Create the authoritative runtime shared_data contract."""
    plant_ids = tuple(config.get("PLANT_IDS", ("lib", "vrfb")))
    startup_transport_mode = config.get("STARTUP_TRANSPORT_MODE", "local")

    if startup_transport_mode not in ["local", "remote"]:
        logging.warning("Invalid STARTUP_TRANSPORT_MODE '%s', using 'local'", startup_transport_mode)
        startup_transport_mode = "local"

    return {
        "session_logs": [],
        "log_lock": threading.Lock(),
        "manual_schedule_df_by_plant": _empty_df_by_plant(plant_ids),
        "manual_schedule_draft_series_df_by_key": _empty_manual_series_df_by_key(),
        "manual_schedule_series_df_by_key": _empty_manual_series_df_by_key(),
        "manual_schedule_merge_enabled_by_key": _default_manual_merge_enabled_by_key(),
        "manual_series_runtime_state_by_key": _default_manual_series_runtime_state_by_key(),
        "api_schedule_df_by_plant": _empty_df_by_plant(plant_ids),
        "transport_mode": startup_transport_mode,
        "scheduler_running_by_plant": {plant_id: False for plant_id in plant_ids},
        "plant_transition_by_plant": {plant_id: "stopped" for plant_id in plant_ids},
        "measurements_filename_by_plant": {plant_id: None for plant_id in plant_ids},
        "current_file_path_by_plant": {plant_id: None for plant_id in plant_ids},
        "current_file_df_by_plant": _empty_df_by_plant(plant_ids),
        "pending_rows_by_file": {},
        "measurements_df": pd.DataFrame(),
        "measurement_post_status": _default_measurement_post_status_by_plant(plant_ids),
        "local_emulator_soc_seed_request_by_plant": _default_local_emulator_soc_seed_request_by_plant(plant_ids),
        "local_emulator_soc_seed_result_by_plant": _default_local_emulator_soc_seed_result_by_plant(plant_ids),
        "posting_runtime": _default_posting_runtime(config.get("ISTENTORE_POST_MEASUREMENTS_IN_API_MODE", True)),
        "api_password": None,
        "api_connection_runtime": _default_api_connection_runtime(),
        "data_fetcher_status": {
            "connected": False,
            "today_fetched": False,
            "tomorrow_fetched": False,
            "today_date": None,
            "tomorrow_date": None,
            "today_points": 0,
            "tomorrow_points": 0,
            "today_points_by_plant": {plant_id: 0 for plant_id in plant_ids},
            "tomorrow_points_by_plant": {plant_id: 0 for plant_id in plant_ids},
            "last_attempt": None,
            "error": None,
        },
        "transport_switching": False,
        "control_command_queue": queue.Queue(maxsize=128),
        "control_command_status_by_id": {},
        "control_command_history_ids": [],
        "control_command_active_id": None,
        "control_command_next_id": 1,
        "plant_observed_state_by_plant": _default_plant_observed_state_by_plant(plant_ids),
        "plant_operating_state_by_plant": _default_plant_operating_state_by_plant(plant_ids),
        "dispatch_write_status_by_plant": default_dispatch_write_status_by_plant(plant_ids),
        "control_engine_status": default_engine_status(include_last_observed_refresh=True),
        "settings_command_queue": queue.Queue(maxsize=128),
        "settings_command_status_by_id": {},
        "settings_command_history_ids": [],
        "settings_command_active_id": None,
        "settings_command_next_id": 1,
        "settings_engine_status": default_engine_status(include_last_observed_refresh=False),
        "lock": threading.Lock(),
        "shutdown_event": threading.Event(),
        "log_file_path": None,
    }


def main():
    """Director agent: load config, initialize shared runtime, and start agents."""
    config = load_config("config.yaml")
    shared_data = build_initial_shared_data(config)

    setup_logging(config, shared_data)
    logging.info("Director agent starting the application.")

    threads = []
    try:
        threads = [
            threading.Thread(target=data_fetcher_agent, args=(config, shared_data), daemon=True),
            threading.Thread(target=scheduler_agent, args=(config, shared_data), daemon=True),
            threading.Thread(target=plant_agent, args=(config, shared_data), daemon=True),
            threading.Thread(target=measurement_agent, args=(config, shared_data), daemon=True),
            threading.Thread(target=control_engine_agent, args=(config, shared_data), daemon=True),
            threading.Thread(target=settings_engine_agent, args=(config, shared_data), daemon=True),
            threading.Thread(target=dashboard_agent, args=(config, shared_data), daemon=True),
        ]

        for thread in threads:
            thread.start()

        logging.info("All agents started.")
        logging.info("Dashboard available at http://127.0.0.1:8050/")

        while not shared_data["shutdown_event"].is_set():
            time.sleep(1)

    except KeyboardInterrupt:
        logging.info("Keyboard interrupt received. Shutting down...")
    except Exception as exc:
        logging.error("An unexpected error occurred in the director: %s", exc)
    finally:
        logging.info("Director initiating shutdown...")
        shared_data["shutdown_event"].set()

        for thread in threads:
            thread.join(timeout=10)

        logging.info("Application shutdown complete.")


if __name__ == "__main__":
    main()
