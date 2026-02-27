"""Shared control-flow helpers for control-engine safe-stop and transport switches."""

import logging
import time

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
    logging.info("Control flow: safe-stop requested for %s.", plant_id.upper())
    with shared_data["lock"]:
        shared_data["scheduler_running_by_plant"][plant_id] = False
        shared_data["plant_transition_by_plant"][plant_id] = "stopping"
    logging.info("Control flow: %s scheduler gate set to False.", plant_id.upper())

    zero_ok = send_setpoints(plant_id, 0.0, 0.0)
    if zero_ok:
        logging.info("Control flow: %s zero setpoints written.", plant_id.upper())
    else:
        logging.warning("Control flow: %s zero setpoints write failed.", plant_id.upper())

    wait_started = time.monotonic()
    reached = wait_until_battery_power_below_threshold(
        plant_id,
        threshold_kw=threshold_kw,
        timeout_s=timeout_s,
    )
    wait_elapsed_s = time.monotonic() - wait_started
    if not reached:
        logging.warning(
            "Control flow: safe stop threshold not reached for %s after %.2fs. Forcing disable.",
            plant_id.upper(),
            wait_elapsed_s,
        )
    else:
        logging.info(
            "Control flow: %s battery power decayed below threshold in %.2fs.",
            plant_id.upper(),
            wait_elapsed_s,
        )

    disable_ok = set_enable(plant_id, 0)
    if disable_ok:
        logging.info("Control flow: %s disable command successful.", plant_id.upper())
    else:
        logging.error("Control flow: %s disable command failed.", plant_id.upper())

    with shared_data["lock"]:
        shared_data["plant_transition_by_plant"][plant_id] = "stopped" if disable_ok else "unknown"

    result = {
        "threshold_reached": bool(reached),
        "disable_ok": bool(disable_ok),
    }
    logging.info(
        "Control flow: safe-stop completed for %s (threshold_reached=%s disable_ok=%s).",
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
        logging.info("Control flow: transport switch requested -> %s", requested_mode)
        with shared_data["lock"]:
            shared_data["transport_switching"] = True

        safe_stop_all_plants_fn()

        with shared_data["lock"]:
            observed_state_map = shared_data.setdefault("plant_observed_state_by_plant", {})
            plant_operating_state_map = shared_data.setdefault("plant_operating_state_by_plant", {})
            dispatch_write_status_map = shared_data.setdefault("dispatch_write_status_by_plant", {})
            for plant_id in plant_ids:
                shared_data["scheduler_running_by_plant"][plant_id] = False
                shared_data["plant_transition_by_plant"][plant_id] = "stopped"
                shared_data["measurements_filename_by_plant"][plant_id] = None
                shared_data["current_file_df_by_plant"][plant_id] = pd.DataFrame()
                shared_data["current_file_path_by_plant"][plant_id] = None
                prev_observed = dict(observed_state_map.get(plant_id, {}) or {})
                prev_observed.update(
                    {
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
                )
                observed_state_map[plant_id] = prev_observed
                plant_operating_state_map[plant_id] = "unknown"
                dispatch_state = dict(dispatch_write_status_map.get(plant_id, {}) or {})
                dispatch_state["sending_enabled"] = False
                dispatch_write_status_map[plant_id] = dispatch_state
            shared_data["transport_mode"] = requested_mode
            shared_data["transport_switching"] = False
        logging.info("Control flow: transport mode switched to %s", requested_mode)
    except Exception as exc:
        logging.error("Control flow: transport switch failed: %s", exc)
        with shared_data["lock"]:
            shared_data["transport_switching"] = False
