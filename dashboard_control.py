"""Dashboard control-flow helpers for safe-stop and global switches."""

import logging

import pandas as pd


def safe_stop_plant(
    shared_data,
    plant_id,
    *,
    send_setpoints,
    wait_until_battery_power_below_threshold,
    set_enable,
    threshold_kw=1.0,
    timeout_s=30,
):
    """Perform safe-stop sequence for one plant and return status payload."""
    logging.info("Dashboard: safe-stop requested for %s.", plant_id.upper())
    with shared_data["lock"]:
        shared_data["scheduler_running_by_plant"][plant_id] = False
        shared_data["plant_transition_by_plant"][plant_id] = "stopping"
    logging.info("Dashboard: %s scheduler gate set to False.", plant_id.upper())

    zero_ok = send_setpoints(plant_id, 0.0, 0.0)
    if zero_ok:
        logging.info("Dashboard: %s zero setpoints written.", plant_id.upper())
    else:
        logging.warning("Dashboard: %s zero setpoints write failed.", plant_id.upper())

    reached = wait_until_battery_power_below_threshold(
        plant_id,
        threshold_kw=threshold_kw,
        timeout_s=timeout_s,
    )
    if not reached:
        logging.warning("Dashboard: safe stop timeout for %s. Forcing disable.", plant_id.upper())
    else:
        logging.info("Dashboard: %s battery power decayed below threshold.", plant_id.upper())

    disable_ok = set_enable(plant_id, 0)
    if disable_ok:
        logging.info("Dashboard: %s disable command successful.", plant_id.upper())
    else:
        logging.error("Dashboard: %s disable command failed.", plant_id.upper())

    with shared_data["lock"]:
        shared_data["plant_transition_by_plant"][plant_id] = "stopped" if disable_ok else "unknown"

    result = {
        "threshold_reached": bool(reached),
        "disable_ok": bool(disable_ok),
    }
    logging.info(
        "Dashboard: safe-stop completed for %s (threshold_reached=%s disable_ok=%s).",
        plant_id.upper(),
        result["threshold_reached"],
        result["disable_ok"],
    )
    return result


def safe_stop_all_plants(plant_ids, safe_stop_plant_fn):
    """Apply safe-stop for each plant and return results map."""
    results = {}
    for plant_id in plant_ids:
        results[plant_id] = safe_stop_plant_fn(plant_id)
    return results


def perform_transport_switch(shared_data, plant_ids, requested_mode, safe_stop_all_plants_fn):
    """Perform guarded global transport switch with stop/reset semantics."""
    try:
        logging.info("Dashboard: transport switch requested -> %s", requested_mode)
        with shared_data["lock"]:
            shared_data["transport_switching"] = True

        safe_stop_all_plants_fn()

        with shared_data["lock"]:
            for plant_id in plant_ids:
                shared_data["scheduler_running_by_plant"][plant_id] = False
                shared_data["plant_transition_by_plant"][plant_id] = "stopped"
                shared_data["measurements_filename_by_plant"][plant_id] = None
                shared_data["current_file_df_by_plant"][plant_id] = pd.DataFrame()
                shared_data["current_file_path_by_plant"][plant_id] = None
            shared_data["transport_mode"] = requested_mode
            shared_data["transport_switching"] = False
        logging.info("Dashboard: transport mode switched to %s", requested_mode)
    except Exception as exc:
        logging.error("Dashboard: transport switch failed: %s", exc)
        with shared_data["lock"]:
            shared_data["transport_switching"] = False


def perform_source_switch(shared_data, requested_source, safe_stop_all_plants_fn):
    """Perform guarded global schedule-source switch with safe-stop."""
    try:
        logging.info("Dashboard: schedule source switch requested -> %s", requested_source)
        with shared_data["lock"]:
            shared_data["schedule_switching"] = True

        safe_stop_all_plants_fn()

        with shared_data["lock"]:
            shared_data["active_schedule_source"] = requested_source
            shared_data["schedule_switching"] = False
        logging.info("Dashboard: active schedule source switched to %s", requested_source)
    except Exception as exc:
        logging.error("Dashboard: schedule source switch failed: %s", exc)
        with shared_data["lock"]:
            shared_data["schedule_switching"] = False
