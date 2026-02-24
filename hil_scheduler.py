import logging
import threading
import time

import pandas as pd

from config_loader import load_config
from dashboard_agent import dashboard_agent
from data_fetcher_agent import data_fetcher_agent
from logger_config import setup_logging
from measurement_agent import measurement_agent
from plant_agent import plant_agent
from scheduler_agent import scheduler_agent


def _empty_df_by_plant(plant_ids):
    return {plant_id: pd.DataFrame() for plant_id in plant_ids}


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


def build_initial_shared_data(config):
    """Create the authoritative runtime shared_data contract."""
    plant_ids = tuple(config.get("PLANT_IDS", ("lib", "vrfb")))
    startup_schedule_source = config.get("STARTUP_SCHEDULE_SOURCE", "manual")
    startup_transport_mode = config.get("STARTUP_TRANSPORT_MODE", "local")

    if startup_schedule_source not in ["manual", "api"]:
        logging.warning("Invalid STARTUP_SCHEDULE_SOURCE '%s', using 'manual'", startup_schedule_source)
        startup_schedule_source = "manual"

    if startup_transport_mode not in ["local", "remote"]:
        logging.warning("Invalid STARTUP_TRANSPORT_MODE '%s', using 'local'", startup_transport_mode)
        startup_transport_mode = "local"

    return {
        "session_logs": [],
        "log_lock": threading.Lock(),
        "manual_schedule_df_by_plant": _empty_df_by_plant(plant_ids),
        "api_schedule_df_by_plant": _empty_df_by_plant(plant_ids),
        "active_schedule_source": startup_schedule_source,
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
        "measurement_posting_enabled": bool(config.get("ISTENTORE_POST_MEASUREMENTS_IN_API_MODE", True)),
        "api_password": None,
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
        "schedule_switching": False,
        "transport_switching": False,
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
