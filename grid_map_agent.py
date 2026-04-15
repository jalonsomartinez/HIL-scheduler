"""Background runtime for the dashboard grid map."""

from __future__ import annotations

import logging
import time

from grid_map_runtime import (
    GRID_MAP_SCENARIO_WITH_BATTERY,
    GRID_MAP_SCENARIO_WITHOUT_BATTERY,
    build_dynamic_payload,
    build_power_flow_summary,
    build_topology_cache,
    ensure_grid_map_runtime,
    publish_grid_map_error,
    publish_grid_map_success,
    publish_grid_map_topology,
    publish_grid_map_topology_error,
    run_grid_map_power_flow,
    select_lib_power_inputs,
    summarize_topology_cache,
    write_grid_map_optional_voltage_point,
)
from time_utils import now_tz


def grid_map_agent(config, shared_data):
    """Run periodic power-flow updates for the grid-map page."""
    logging.info("Grid map agent started.")

    period_s = float(config.get("GRID_MAP_PERIOD_S", 5.0) or 5.0)
    ensure_grid_map_runtime(shared_data, period_s)

    try:
        topology_cache = build_topology_cache(config)
        publish_grid_map_topology(shared_data, topology_cache=topology_cache)
        logging.info(
            "Grid map: topology ready (buses=%s lines=%s trafos=%s).",
            summarize_topology_cache(topology_cache).get("bus_count"),
            summarize_topology_cache(topology_cache).get("line_count"),
            summarize_topology_cache(topology_cache).get("trafo_count"),
        )
    except Exception as exc:
        logging.error("Grid map: failed to build topology cache: %s", exc)
        publish_grid_map_topology_error(shared_data, error_text=str(exc))
        topology_cache = None

    while not shared_data["shutdown_event"].is_set():
        loop_start = time.monotonic()
        now_value = now_tz(config)
        try:
            if topology_cache is None:
                raise RuntimeError("Grid-map topology cache is unavailable.")
            input_payload = select_lib_power_inputs(shared_data, config)
            if str(input_payload.get("source")) == "none":
                raise ValueError("No recent LIB measured power is available for the grid map.")
            run_payload = run_grid_map_power_flow(input_payload, config)
            nobat_input_payload = dict(input_payload)
            nobat_input_payload["p_kw"] = 0.0
            nobat_input_payload["q_kvar"] = 0.0
            run_payload_nobat = run_grid_map_power_flow(nobat_input_payload, config)
            power_flow_result = run_payload["power_flow_result"]
            power_flow_result_nobat = run_payload_nobat["power_flow_result"]
            write_grid_map_optional_voltage_point(config, shared_data, run_payload)
            summary = build_power_flow_summary(power_flow_result)
            summary_nobat = build_power_flow_summary(power_flow_result_nobat)
            dynamic_payload = build_dynamic_payload(power_flow_result, topology_cache)
            dynamic_payload_nobat = build_dynamic_payload(power_flow_result_nobat, topology_cache)
            publish_grid_map_success(
                shared_data,
                now_value=now_value,
                input_payload=input_payload,
                run_payload=run_payload,
                summary=summary,
                dynamic_payload=dynamic_payload,
                scenario_results={
                    GRID_MAP_SCENARIO_WITH_BATTERY: {
                        "requested_timestamp_local": run_payload.get("requested_timestamp_local"),
                        "selected_timestamp_local": power_flow_result.get("selected_timestamp_local"),
                        "selected_timestamp_utc": power_flow_result.get("selected_timestamp_utc"),
                        "used_previous_hour_fallback": bool(power_flow_result.get("used_previous_hour_fallback", False)),
                        "battery_input_p_kw": run_payload.get("battery_input_p_kw"),
                        "battery_input_q_kvar": run_payload.get("battery_input_q_kvar"),
                        "battery_input_p_mw": run_payload.get("battery_input_p_mw"),
                        "battery_input_q_mvar": run_payload.get("battery_input_q_mvar"),
                        "summary": summary,
                        "dynamic_payload": dynamic_payload,
                    },
                    GRID_MAP_SCENARIO_WITHOUT_BATTERY: {
                        "requested_timestamp_local": run_payload_nobat.get("requested_timestamp_local"),
                        "selected_timestamp_local": power_flow_result_nobat.get("selected_timestamp_local"),
                        "selected_timestamp_utc": power_flow_result_nobat.get("selected_timestamp_utc"),
                        "used_previous_hour_fallback": bool(power_flow_result_nobat.get("used_previous_hour_fallback", False)),
                        "battery_input_p_kw": run_payload_nobat.get("battery_input_p_kw"),
                        "battery_input_q_kvar": run_payload_nobat.get("battery_input_q_kvar"),
                        "battery_input_p_mw": run_payload_nobat.get("battery_input_p_mw"),
                        "battery_input_q_mvar": run_payload_nobat.get("battery_input_q_mvar"),
                        "summary": summary_nobat,
                        "dynamic_payload": dynamic_payload_nobat,
                    },
                },
            )
        except Exception as exc:
            logging.warning("Grid map: update failed: %s", exc)
            publish_grid_map_error(
                shared_data,
                now_value=now_value,
                error_text=str(exc),
                input_payload=locals().get("input_payload"),
            )

        elapsed = time.monotonic() - loop_start
        time.sleep(max(0.1, period_s - elapsed))
