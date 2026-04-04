"""Background runtime for the dashboard grid map."""

from __future__ import annotations

import logging
import time

from grid_map_runtime import (
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
)
from time_utils import now_tz


def grid_map_agent(config, shared_data):
    """Run periodic power-flow updates for the grid-map page."""
    logging.info("Grid map agent started.")

    period_s = float(config.get("GRID_MAP_PERIOD_S", 5.0) or 5.0)
    ensure_grid_map_runtime(shared_data, period_s)

    try:
        topology_cache = build_topology_cache()
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
            power_flow_result = run_payload["power_flow_result"]
            summary = build_power_flow_summary(power_flow_result)
            dynamic_payload = build_dynamic_payload(power_flow_result, topology_cache)
            publish_grid_map_success(
                shared_data,
                now_value=now_value,
                input_payload=input_payload,
                run_payload=run_payload,
                summary=summary,
                dynamic_payload=dynamic_payload,
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
