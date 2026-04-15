import argparse
import logging
from pathlib import Path
import queue
import sys
import threading
import time

import pandas as pd

from runtime.api_runtime_state import default_api_connection_runtime
from runtime.defaults import default_measurement_post_status_by_plant
from runtime.dispatch_write_runtime import default_dispatch_write_status_by_plant
from runtime.engine_status_runtime import default_engine_status
from grid_map_agent import grid_map_agent
from grid_map_runtime import default_grid_map_runtime
import scheduling.manual_schedule_manager as msm
from config_loader import load_config
from control.engine_agent import control_engine_agent
from dashboard.agent import dashboard_agent
from dashboard.public_agent import public_dashboard_agent
from data_fetcher_agent import data_fetcher_agent
from logger_config import setup_logging
from measurement.agent import measurement_agent
from plant_agent import plant_agent
from runtime.paths import get_project_root
from scheduling.agent import scheduler_agent
from settings.engine_agent import settings_engine_agent


DEFAULT_CONFIG_GLOB = "config*.yaml"
DEFAULT_CONFIG_FILENAME = "config.yaml"


def _empty_df_by_plant(plant_ids):
    return {plant_id: pd.DataFrame() for plant_id in plant_ids}


def _empty_manual_series_df_by_key():
    return msm.default_manual_series_map()

def _default_manual_merge_enabled_by_key():
    return msm.default_manual_merge_enabled_map(default_enabled=False)


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
            "start_command_state": None,
            "stop_command_state": None,
            "q_control_mode_state": None,
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


def _default_reactive_control_mode_by_plant(plant_ids):
    return {plant_id: 1 for plant_id in plant_ids}


def _default_reactive_control_mode_runtime_by_plant(plant_ids):
    return {
        plant_id: {
            "selected_mode": 1,
            "desired_mode": 1,
            "last_command_id": None,
            "last_error": None,
            "last_updated": None,
            "last_success": None,
        }
        for plant_id in plant_ids
    }


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


def _default_mfrr_poll_status(poll_period_s):
    try:
        cadence_s = float(poll_period_s)
    except (TypeError, ValueError):
        cadence_s = 60.0
    if cadence_s <= 0.0:
        cadence_s = 60.0
    return {
        "last_attempt_at": None,
        "last_success_at": None,
        "last_result": "never",
        "last_error": None,
        "last_points_lib": 0,
        "next_scheduled_at": None,
        "poll_period_s": cadence_s,
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
        "api_day_ahead_schedule_df_by_plant": _empty_df_by_plant(plant_ids),
        "api_mfrr_schedule_df_by_plant": _empty_df_by_plant(plant_ids),
        "api_schedule_df_by_plant": _empty_df_by_plant(plant_ids),
        "transport_mode": startup_transport_mode,
        "scheduler_running_by_plant": {plant_id: False for plant_id in plant_ids},
        "plant_transition_by_plant": {plant_id: "stopped" for plant_id in plant_ids},
        "measurements_filename_by_plant": {plant_id: None for plant_id in plant_ids},
        "current_file_path_by_plant": {plant_id: None for plant_id in plant_ids},
        "current_file_df_by_plant": _empty_df_by_plant(plant_ids),
        "pending_rows_by_file": {},
        "twin_measurements_filename": None,
        "twin_current_file_path": None,
        "twin_current_file_df": pd.DataFrame(),
        "pending_twin_rows_by_file": {},
        "twin_nobat_measurements_filename": None,
        "twin_nobat_current_file_path": None,
        "twin_nobat_current_file_df": pd.DataFrame(),
        "pending_twin_nobat_rows_by_file": {},
        "measurements_df": pd.DataFrame(),
        "measurement_post_status": default_measurement_post_status_by_plant(plant_ids),
        "local_emulator_soc_seed_request_by_plant": _default_local_emulator_soc_seed_request_by_plant(plant_ids),
        "local_emulator_soc_seed_result_by_plant": _default_local_emulator_soc_seed_result_by_plant(plant_ids),
        "posting_runtime": _default_posting_runtime(config.get("ISTENTORE_POST_MEASUREMENTS_IN_API_MODE", True)),
        "api_password": config.get("ISTENTORE_API_PASSWORD"),
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
            "mfrr_poll": _default_mfrr_poll_status(config.get("ISTENTORE_MFRR_POLL_PERIOD_S", 60)),
        },
        "transport_switching": False,
        "control_command_queue": queue.Queue(maxsize=128),
        "control_command_status_by_id": {},
        "control_command_history_ids": [],
        "control_command_active_id": None,
        "control_command_next_id": 1,
        "plant_observed_state_by_plant": _default_plant_observed_state_by_plant(plant_ids),
        "plant_operating_state_by_plant": _default_plant_operating_state_by_plant(plant_ids),
        "reactive_control_mode_by_plant": _default_reactive_control_mode_by_plant(plant_ids),
        "reactive_control_mode_runtime_by_plant": _default_reactive_control_mode_runtime_by_plant(plant_ids),
        "dispatch_write_status_by_plant": default_dispatch_write_status_by_plant(plant_ids),
        "control_engine_status": default_engine_status(include_last_observed_refresh=True),
        "settings_command_queue": queue.Queue(maxsize=128),
        "settings_command_status_by_id": {},
        "settings_command_history_ids": [],
        "settings_command_active_id": None,
        "settings_command_next_id": 1,
        "settings_engine_status": default_engine_status(include_last_observed_refresh=False),
        "grid_map_runtime": default_grid_map_runtime(config.get("GRID_MAP_PERIOD_S", 10.0)),
        "lock": threading.Lock(),
        "shutdown_event": threading.Event(),
        "log_file_path": None,
    }


def build_agent_threads(config, shared_data):
    threads = [
        threading.Thread(target=data_fetcher_agent, args=(config, shared_data), daemon=True),
        threading.Thread(target=scheduler_agent, args=(config, shared_data), daemon=True),
        threading.Thread(target=plant_agent, args=(config, shared_data), daemon=True),
        threading.Thread(target=measurement_agent, args=(config, shared_data), daemon=True),
        threading.Thread(target=grid_map_agent, args=(config, shared_data), daemon=True),
        threading.Thread(target=control_engine_agent, args=(config, shared_data), daemon=True),
        threading.Thread(target=settings_engine_agent, args=(config, shared_data), daemon=True),
        threading.Thread(target=dashboard_agent, args=(config, shared_data), daemon=True),
    ]
    if bool(config.get("DASHBOARD_PUBLIC_READONLY_ENABLED", False)):
        threads.append(threading.Thread(target=public_dashboard_agent, args=(config, shared_data), daemon=True))
    return threads


def parse_startup_args(argv=None):
    parser = argparse.ArgumentParser(description="Run the HIL Scheduler runtime.")
    parser.add_argument(
        "--config",
        help="Config file path or repo-root filename. Defaults to smart selection among config*.yaml.",
    )
    return parser.parse_args(argv)


def discover_config_profiles(project_root):
    root = Path(project_root)
    return sorted(
        path for path in root.glob(DEFAULT_CONFIG_GLOB) if path.is_file()
    )


def resolve_config_argument(config_arg, project_root):
    candidate = Path(config_arg).expanduser()
    if not candidate.is_absolute():
        candidate = Path(project_root) / candidate
    candidate = candidate.resolve(strict=False)
    if not candidate.is_file():
        raise FileNotFoundError(
            f"Configuration file not found: {candidate}. "
            f"Pass an existing path with --config."
        )
    return candidate


def _default_config_index(config_paths):
    for index, path in enumerate(config_paths):
        if path.name == DEFAULT_CONFIG_FILENAME:
            return index
    return None


def prompt_for_config_selection(config_paths, input_fn=input, output_stream=None):
    if not config_paths:
        raise RuntimeError("No configuration profiles were provided for selection.")

    stream = output_stream or sys.stdout
    default_index = _default_config_index(config_paths)

    print("Multiple startup config profiles found:", file=stream)
    for index, path in enumerate(config_paths, start=1):
        label = path.name
        if default_index is not None and index - 1 == default_index:
            label = f"{label} [default]"
        print(f"  {index}. {label}", file=stream)

    if default_index is not None:
        prompt = f"Select config profile [Enter={default_index + 1}]: "
    else:
        prompt = "Select config profile [1-{}]: ".format(len(config_paths))

    while True:
        choice = str(input_fn(prompt)).strip()
        if not choice:
            if default_index is not None:
                return config_paths[default_index]
            print("Please enter a profile number.", file=stream)
            continue
        if choice.isdigit():
            selected_index = int(choice) - 1
            if 0 <= selected_index < len(config_paths):
                return config_paths[selected_index]
        print(
            f"Invalid selection '{choice}'. Enter a number between 1 and {len(config_paths)}.",
            file=stream,
        )


def select_startup_config_path(argv=None, project_root=None, stdin_isatty=None, input_fn=input, output_stream=None):
    args = parse_startup_args(argv)
    root = Path(project_root) if project_root is not None else Path(get_project_root(__file__))

    if args.config:
        return resolve_config_argument(args.config, root)

    config_paths = discover_config_profiles(root)
    if not config_paths:
        raise FileNotFoundError(
            f"No configuration profiles matching {DEFAULT_CONFIG_GLOB!r} were found in {root}."
        )
    if len(config_paths) == 1:
        return config_paths[0]

    interactive = bool(stdin_isatty) if stdin_isatty is not None else sys.stdin.isatty()
    if not interactive:
        available = ", ".join(path.name for path in config_paths)
        raise RuntimeError(
            "Multiple configuration profiles were found in "
            f"{root}: {available}. Pass --config <filename> to select one."
        )
    return prompt_for_config_selection(config_paths, input_fn=input_fn, output_stream=output_stream)


def main(argv=None):
    """Director agent: load config, initialize shared runtime, and start agents."""
    config_path = select_startup_config_path(argv)
    config = load_config(str(config_path))
    shared_data = build_initial_shared_data(config)

    setup_logging(config, shared_data)
    logging.info("Selected startup config: %s", config_path)
    logging.info("Director agent starting the application.")

    threads = []
    try:
        threads = build_agent_threads(config, shared_data)

        for thread in threads:
            thread.start()

        logging.info("All agents started.")
        private_host = str(config.get("DASHBOARD_PRIVATE_HOST", "127.0.0.1"))
        private_port = int(config.get("DASHBOARD_PRIVATE_PORT", 8050))
        logging.info("Private dashboard available at http://%s:%s/", private_host, private_port)
        if bool(config.get("DASHBOARD_PUBLIC_READONLY_ENABLED", False)):
            public_host = str(config.get("DASHBOARD_PUBLIC_READONLY_HOST", "127.0.0.1"))
            public_port = int(config.get("DASHBOARD_PUBLIC_READONLY_PORT", 8060))
            logging.info("Public read-only dashboard available at http://%s:%s/", public_host, public_port)

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
