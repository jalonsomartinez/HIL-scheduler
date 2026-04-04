"""Grid-map runtime helpers.

This module owns the runtime contract shared between the background grid-map
agent and the dashboard callbacks. Heavy pandapower imports are kept lazy so
test environments can import this module without the dependency installed.
"""

from __future__ import annotations

import copy
import importlib
import json
import logging
import math
from typing import Any

import pandas as pd
import plotly.graph_objects as go

from dashboard.plotting import DEFAULT_PLOT_THEME
from time_utils import get_config_tz, normalize_datetime_series, normalize_timestamp_value, serialize_iso_with_tz

GRID_MAP_VOLTAGE_MIN_PU = 0.95
GRID_MAP_VOLTAGE_MAX_PU = 1.05
GRID_MAP_LINE_LOADING_LIMIT_PCT = 100.0
GRID_MAP_STATUS_KEY = "grid_map_runtime"
GRID_MAP_SOURCE_CRS = "EPSG:32630"
GRID_MAP_TARGET_CRS = "EPSG:4326"
GRID_MAP_MAP_STYLE = "open-street-map"

_SIMULATOR_MODULE = None


def default_grid_map_runtime(period_s: float) -> dict[str, Any]:
    try:
        cadence_s = float(period_s)
    except (TypeError, ValueError):
        cadence_s = 5.0
    if cadence_s <= 0.0:
        cadence_s = 5.0
    return {
        "state": "idle",
        "poll_period_s": cadence_s,
        "topology_ready": False,
        "topology_error": None,
        "topology_cache": None,
        "topology_cache_meta": None,
        "last_run_at": None,
        "last_success_at": None,
        "last_error": None,
        "requested_timestamp_local": None,
        "selected_timestamp_local": None,
        "selected_timestamp_utc": None,
        "used_previous_hour_fallback": False,
        "input_source": "none",
        "input_measured_at": None,
        "battery_input_p_kw": None,
        "battery_input_q_kvar": None,
        "battery_input_p_mw": None,
        "battery_input_q_mvar": None,
        "summary": None,
        "dynamic_payload": None,
        "coordinate_mode": "schematic",
        "source_crs": None,
        "target_crs": None,
        "map_background_enabled": False,
        "map_background_reason": "topology_unavailable",
        "stale": True,
    }


def ensure_grid_map_runtime(shared_data: dict[str, Any], period_s: float) -> None:
    with shared_data["lock"]:
        existing = shared_data.get(GRID_MAP_STATUS_KEY)
        if isinstance(existing, dict):
            merged = default_grid_map_runtime(period_s)
            merged.update(existing)
            merged["poll_period_s"] = float(merged.get("poll_period_s", period_s) or period_s)
            shared_data[GRID_MAP_STATUS_KEY] = merged
            return
        shared_data[GRID_MAP_STATUS_KEY] = default_grid_map_runtime(period_s)


def _import_simulator_module():
    global _SIMULATOR_MODULE
    if _SIMULATOR_MODULE is not None:
        return _SIMULATOR_MODULE
    _SIMULATOR_MODULE = importlib.import_module("grid_map_digital_twin.simulator")
    return _SIMULATOR_MODULE


def _coerce_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _is_missing_geo_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    try:
        return bool(pd.isna(value))
    except Exception:
        return False


def _series_from_results(results_tables: dict[str, Any], table_name: str, column_name: str) -> pd.Series:
    table = (results_tables or {}).get(table_name)
    if not isinstance(table, pd.DataFrame) or table.empty or column_name not in table.columns:
        return pd.Series(dtype=float)
    series = pd.to_numeric(table[column_name], errors="coerce")
    series.index = pd.Index(table.index)
    return series


def _parse_bus_geodata(net: Any) -> dict[int, tuple[float, float]]:
    coords: dict[int, tuple[float, float]] = {}

    bus_geodata = getattr(net, "bus_geodata", None)
    if isinstance(bus_geodata, pd.DataFrame) and not bus_geodata.empty:
        for bus_index, row in bus_geodata.iterrows():
            x = _coerce_float(row.get("x"))
            y = _coerce_float(row.get("y"))
            if x is None or y is None:
                continue
            coords[int(bus_index)] = (float(x), float(y))

    if coords:
        return coords

    bus_df = getattr(net, "bus", None)
    if not isinstance(bus_df, pd.DataFrame) or bus_df.empty or "geo" not in bus_df.columns:
        return coords

    for bus_index, raw_geo in bus_df["geo"].items():
        if _is_missing_geo_value(raw_geo):
            continue
        try:
            payload = json.loads(raw_geo) if isinstance(raw_geo, str) else raw_geo
        except Exception:
            continue
        if not isinstance(payload, dict) or str(payload.get("type")) != "Point":
            continue
        coordinates = payload.get("coordinates") or []
        if len(coordinates) < 2:
            continue
        x = _coerce_float(coordinates[0])
        y = _coerce_float(coordinates[1])
        if x is None or y is None:
            continue
        coords[int(bus_index)] = (float(x), float(y))
    return coords


def _parse_bus_geojson_coords(net: Any) -> dict[int, tuple[float, float]]:
    coords: dict[int, tuple[float, float]] = {}
    bus_df = getattr(net, "bus", None)
    if not isinstance(bus_df, pd.DataFrame) or bus_df.empty or "geo" not in bus_df.columns:
        return coords

    for bus_index, raw_geo in bus_df["geo"].items():
        if _is_missing_geo_value(raw_geo):
            continue
        try:
            payload = json.loads(raw_geo) if isinstance(raw_geo, str) else raw_geo
        except Exception:
            continue
        if not isinstance(payload, dict) or str(payload.get("type")) != "Point":
            continue
        coordinates = payload.get("coordinates") or []
        if len(coordinates) < 2:
            continue
        x = _coerce_float(coordinates[0])
        y = _coerce_float(coordinates[1])
        if x is None or y is None:
            continue
        coords[int(bus_index)] = (float(x), float(y))
    return coords


def _parse_line_geodata(net: Any, bus_coords: dict[int, tuple[float, float]]) -> dict[int, list[tuple[float, float]]]:
    line_coords: dict[int, list[tuple[float, float]]] = {}

    line_geodata = getattr(net, "line_geodata", None)
    if isinstance(line_geodata, pd.DataFrame) and not line_geodata.empty:
        for line_index, row in line_geodata.iterrows():
            raw_coords = row.get("coords")
            if not isinstance(raw_coords, (list, tuple)) or len(raw_coords) < 2:
                continue
            path = []
            for point in raw_coords:
                if not isinstance(point, (list, tuple)) or len(point) < 2:
                    continue
                x = _coerce_float(point[0])
                y = _coerce_float(point[1])
                if x is None or y is None:
                    continue
                path.append((float(x), float(y)))
            if len(path) >= 2:
                line_coords[int(line_index)] = path

    if line_coords:
        return line_coords

    line_df = getattr(net, "line", None)
    if isinstance(line_df, pd.DataFrame) and not line_df.empty and "geo" in line_df.columns:
        for line_index, raw_geo in line_df["geo"].items():
            if _is_missing_geo_value(raw_geo):
                continue
            try:
                payload = json.loads(raw_geo) if isinstance(raw_geo, str) else raw_geo
            except Exception:
                continue
            if not isinstance(payload, dict) or str(payload.get("type")) != "LineString":
                continue
            coordinates = payload.get("coordinates") or []
            path = []
            for point in coordinates:
                if not isinstance(point, (list, tuple)) or len(point) < 2:
                    continue
                x = _coerce_float(point[0])
                y = _coerce_float(point[1])
                if x is None or y is None:
                    continue
                path.append((float(x), float(y)))
            if len(path) >= 2:
                line_coords[int(line_index)] = path

    if line_coords:
        return line_coords

    if not isinstance(line_df, pd.DataFrame) or line_df.empty:
        return line_coords

    for line_index, row in line_df.iterrows():
        from_bus = int(row.get("from_bus"))
        to_bus = int(row.get("to_bus"))
        from_coord = bus_coords.get(from_bus)
        to_coord = bus_coords.get(to_bus)
        if from_coord is None or to_coord is None:
            continue
        line_coords[int(line_index)] = [from_coord, to_coord]
    return line_coords


def _normalize_geojson_components_for_convert_crs(net: Any) -> None:
    bus_df = getattr(net, "bus", None)
    if isinstance(bus_df, pd.DataFrame) and not bus_df.empty and "geo" in bus_df.columns:
        bus_rows = []
        bus_index = []
        for bus_id, raw_geo in bus_df["geo"].items():
            if _is_missing_geo_value(raw_geo):
                continue
            try:
                payload = json.loads(raw_geo) if isinstance(raw_geo, str) else raw_geo
            except Exception:
                continue
            if not isinstance(payload, dict) or str(payload.get("type")) != "Point":
                continue
            coordinates = payload.get("coordinates") or []
            if len(coordinates) < 2:
                continue
            x = _coerce_float(coordinates[0])
            y = _coerce_float(coordinates[1])
            if x is None or y is None:
                continue
            bus_index.append(int(bus_id))
            bus_rows.append({"x": float(x), "y": float(y)})
        if bus_rows:
            net.bus_geodata = pd.DataFrame(bus_rows, index=bus_index)
        bus_df["geo"] = pd.NA

    line_df = getattr(net, "line", None)
    if isinstance(line_df, pd.DataFrame) and not line_df.empty and "geo" in line_df.columns:
        line_rows = []
        line_index = []
        for line_id, raw_geo in line_df["geo"].items():
            if _is_missing_geo_value(raw_geo):
                continue
            try:
                payload = json.loads(raw_geo) if isinstance(raw_geo, str) else raw_geo
            except Exception:
                continue
            if not isinstance(payload, dict) or str(payload.get("type")) != "LineString":
                continue
            coordinates = payload.get("coordinates") or []
            coords = []
            for point in coordinates:
                if not isinstance(point, (list, tuple)) or len(point) < 2:
                    continue
                x = _coerce_float(point[0])
                y = _coerce_float(point[1])
                if x is None or y is None:
                    continue
                coords.append((float(x), float(y)))
            if len(coords) < 2:
                continue
            line_index.append(int(line_id))
            line_rows.append({"coords": coords})
        if line_rows:
            net.line_geodata = pd.DataFrame(line_rows, index=line_index)
        line_df["geo"] = pd.NA


def _parse_trafo_paths(net: Any, bus_coords: dict[int, tuple[float, float]]) -> dict[int, list[tuple[float, float]]]:
    trafo_df = getattr(net, "trafo", None)
    if not isinstance(trafo_df, pd.DataFrame) or trafo_df.empty:
        return {}

    paths = {}
    for trafo_index, row in trafo_df.iterrows():
        hv_bus = int(row.get("hv_bus"))
        lv_bus = int(row.get("lv_bus"))
        hv_coord = bus_coords.get(hv_bus)
        lv_coord = bus_coords.get(lv_bus)
        if hv_coord is None or lv_coord is None:
            continue
        paths[int(trafo_index)] = [hv_coord, lv_coord]
    return paths


def _line_center(path: list[tuple[float, float]]) -> tuple[float, float]:
    if not path:
        return (0.0, 0.0)
    point = path[len(path) // 2]
    return float(point[0]), float(point[1])


def _has_bus_geodata(net: Any) -> bool:
    bus_geodata = getattr(net, "bus_geodata", None)
    if isinstance(bus_geodata, pd.DataFrame) and not bus_geodata.empty:
        return True
    bus_df = getattr(net, "bus", None)
    if isinstance(bus_df, pd.DataFrame) and "geo" in bus_df.columns:
        return bool(bus_df["geo"].fillna("").astype(str).str.strip().ne("").any())
    return False


def _bounds_for_coords(coords: dict[int, tuple[float, float]]) -> dict[str, float] | None:
    if not coords:
        return None
    xs = [coord[0] for coord in coords.values()]
    ys = [coord[1] for coord in coords.values()]
    return {
        "x_min": float(min(xs)),
        "x_max": float(max(xs)),
        "y_min": float(min(ys)),
        "y_max": float(max(ys)),
    }


def _center_for_bounds(bounds: dict[str, float] | None) -> dict[str, float] | None:
    if not isinstance(bounds, dict):
        return None
    x_min = _coerce_float(bounds.get("x_min"))
    x_max = _coerce_float(bounds.get("x_max"))
    y_min = _coerce_float(bounds.get("y_min"))
    y_max = _coerce_float(bounds.get("y_max"))
    if None in (x_min, x_max, y_min, y_max):
        return None
    return {
        "lon": float((x_min + x_max) / 2.0),
        "lat": float((y_min + y_max) / 2.0),
    }


def _build_initial_plotly_figure(net: Any) -> dict[str, Any] | None:
    try:
        from pandapower.plotting import create_generic_coordinates
        from pandapower.plotting.plotly import create_bus_trace, create_line_trace, create_trafo_trace, draw_traces
    except Exception as exc:
        logging.warning("Grid map: pandapower plotly helpers unavailable: %s", exc)
        return None

    try:
        plot_net = copy.deepcopy(net)
        if not _has_bus_geodata(plot_net):
            create_generic_coordinates(plot_net, overwrite=False)

        traces = []
        try:
            line_trace = create_line_trace(plot_net, lines=plot_net.line.index, use_line_geo=True)
            if isinstance(line_trace, list):
                traces.extend(line_trace)
            elif line_trace is not None:
                traces.append(line_trace)
        except TypeError:
            line_trace = create_line_trace(plot_net, lines=plot_net.line.index)
            if isinstance(line_trace, list):
                traces.extend(line_trace)
            elif line_trace is not None:
                traces.append(line_trace)

        if hasattr(plot_net, "trafo") and isinstance(plot_net.trafo, pd.DataFrame) and not plot_net.trafo.empty:
            trafo_trace = create_trafo_trace(plot_net, trafos=plot_net.trafo.index)
            if isinstance(trafo_trace, list):
                traces.extend(trafo_trace)
            elif trafo_trace is not None:
                traces.append(trafo_trace)

        bus_trace = create_bus_trace(plot_net, buses=plot_net.bus.index)
        if isinstance(bus_trace, list):
            traces.extend(bus_trace)
        elif bus_trace is not None:
            traces.append(bus_trace)

        if not traces:
            return None

        try:
            fig = draw_traces(traces, showlegend=False, filename=None, auto_open=False)
        except TypeError:
            fig = draw_traces(traces, showlegend=False)
        if hasattr(fig, "to_dict"):
            return fig.to_dict()
        return None
    except Exception as exc:
        logging.warning("Grid map: pandapower initial Plotly figure unavailable; falling back to native topology rendering: %s", exc)
        return None


def build_topology_cache() -> dict[str, Any]:
    simulator_module = _import_simulator_module()
    if hasattr(simulator_module, "get_base_network_copy"):
        net = simulator_module.get_base_network_copy()
    else:
        assets = simulator_module._load_assets()
        net = copy.deepcopy(assets["base_net"])
    metadata = dict(getattr(simulator_module, "get_metadata", lambda: {})() or {})

    try:
        from pandapower.plotting import create_generic_coordinates
    except Exception as exc:
        raise RuntimeError("pandapower plotting helpers are unavailable") from exc

    try:
        from pandapower.plotting.geo import convert_crs
    except Exception:
        convert_crs = None

    projected_bus_coords = _parse_bus_geojson_coords(net)

    generated_coordinates = False
    if not projected_bus_coords and not _has_bus_geodata(net):
        create_generic_coordinates(net, overwrite=False)
        generated_coordinates = True

    bus_coords = _parse_bus_geodata(net)
    if projected_bus_coords:
        bus_coords = projected_bus_coords
    if not projected_bus_coords and not bus_coords:
        raise RuntimeError("No bus geodata available after topology preparation.")

    line_paths = _parse_line_geodata(net, bus_coords)
    trafo_paths = _parse_trafo_paths(net, bus_coords)
    bounds = _bounds_for_coords(bus_coords)

    coordinate_mode = "schematic"
    source_crs = None
    target_crs = None
    map_background_enabled = False
    map_background_reason = "generated_coordinates" if generated_coordinates else "no_geographic_coordinates"
    geographic_bus_coords: dict[int, tuple[float, float]] = {}
    geographic_line_paths: dict[int, list[tuple[float, float]]] = {}
    geographic_trafo_paths: dict[int, list[tuple[float, float]]] = {}
    geographic_bounds = None
    geographic_center = None

    if projected_bus_coords:
        try:
            if convert_crs is None:
                raise RuntimeError("pandapower CRS conversion helper unavailable")
            geographic_net = copy.deepcopy(net)
            _normalize_geojson_components_for_convert_crs(geographic_net)
            convert_crs(geographic_net, epsg_in=32630, epsg_out=4326)
            geographic_bus_coords = _parse_bus_geodata(geographic_net)
            if geographic_bus_coords:
                geographic_line_paths = _parse_line_geodata(geographic_net, geographic_bus_coords)
                geographic_trafo_paths = _parse_trafo_paths(geographic_net, geographic_bus_coords)
                geographic_bounds = _bounds_for_coords(geographic_bus_coords)
                geographic_center = _center_for_bounds(geographic_bounds)
                coordinate_mode = "geographic"
                source_crs = GRID_MAP_SOURCE_CRS
                target_crs = GRID_MAP_TARGET_CRS
                map_background_enabled = True
                map_background_reason = None
            else:
                map_background_reason = "coordinate_conversion_empty"
        except Exception as exc:
            logging.warning("Grid map: pandapower CRS conversion failed: %s", exc)
            map_background_reason = f"coordinate_conversion_failed:{exc}"

    return {
        "initial_figure": _build_initial_plotly_figure(net),
        "metadata": metadata,
        "coordinate_mode": coordinate_mode,
        "source_crs": source_crs,
        "target_crs": target_crs,
        "map_background_enabled": map_background_enabled,
        "map_background_reason": map_background_reason,
        "bounds": dict(bounds or {}),
        "bus_order": [int(index) for index in sorted(bus_coords.keys())],
        "bus_coords": {str(index): [float(point[0]), float(point[1])] for index, point in bus_coords.items()},
        "line_order": [int(index) for index in sorted(line_paths.keys())],
        "line_paths": {
            str(index): [[float(point[0]), float(point[1])] for point in path]
            for index, path in line_paths.items()
        },
        "trafo_order": [int(index) for index in sorted(trafo_paths.keys())],
        "trafo_paths": {
            str(index): [[float(point[0]), float(point[1])] for point in path]
            for index, path in trafo_paths.items()
        },
        "geographic_bounds": dict(geographic_bounds or {}),
        "geographic_center": dict(geographic_center or {}),
        "geographic_bus_coords": {
            str(index): [float(point[0]), float(point[1])] for index, point in geographic_bus_coords.items()
        },
        "geographic_line_paths": {
            str(index): [[float(point[0]), float(point[1])] for point in path]
            for index, path in geographic_line_paths.items()
        },
        "geographic_trafo_paths": {
            str(index): [[float(point[0]), float(point[1])] for point in path]
            for index, path in geographic_trafo_paths.items()
        },
    }


def summarize_topology_cache(topology_cache: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(topology_cache, dict):
        return None
    return {
        "bus_count": len(topology_cache.get("bus_order", [])),
        "line_count": len(topology_cache.get("line_order", [])),
        "trafo_count": len(topology_cache.get("trafo_order", [])),
        "initial_figure_ready": bool(topology_cache.get("initial_figure")),
        "coordinate_mode": str(topology_cache.get("coordinate_mode") or "schematic"),
        "source_crs": topology_cache.get("source_crs"),
        "target_crs": topology_cache.get("target_crs"),
        "map_background_enabled": bool(topology_cache.get("map_background_enabled", False)),
        "map_background_reason": topology_cache.get("map_background_reason"),
        "metadata": dict(topology_cache.get("metadata", {}) or {}),
    }


def select_lib_power_inputs(shared_data: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    tz = get_config_tz(config)
    with shared_data["lock"]:
        observed = dict(((shared_data.get("plant_observed_state_by_plant", {}) or {}).get("lib", {}) or {}))
        current_df = ((shared_data.get("current_file_df_by_plant", {}) or {}).get("lib"))
        current_df = current_df.copy() if isinstance(current_df, pd.DataFrame) else pd.DataFrame()

    observed_p = _coerce_float(observed.get("p_battery_kw"))
    observed_q = _coerce_float(observed.get("q_battery_kvar"))
    observed_stale = bool(observed.get("stale", True))
    observed_at = observed.get("last_success") or observed.get("last_attempt")
    observed_ts = normalize_timestamp_value(observed_at, tz) if observed_at is not None else pd.NaT
    if observed_p is not None and observed_q is not None and not observed_stale and not pd.isna(observed_ts):
        return {
            "source": "observed_state",
            "timestamp": observed_ts,
            "p_kw": observed_p,
            "q_kvar": observed_q,
        }

    if isinstance(current_df, pd.DataFrame) and not current_df.empty and "timestamp" in current_df.columns:
        history_df = current_df.copy()
        history_df["__ts"] = normalize_datetime_series(history_df["timestamp"], tz)
        history_df = history_df.dropna(subset=["__ts"]).sort_values("__ts")
        if not history_df.empty:
            latest = history_df.iloc[-1]
            p_kw = _coerce_float(latest.get("battery_active_power_kw"))
            q_kvar = _coerce_float(latest.get("battery_reactive_power_kvar"))
            if p_kw is not None and q_kvar is not None:
                return {
                    "source": "measurement_cache",
                    "timestamp": normalize_timestamp_value(latest.get("__ts"), tz),
                    "p_kw": p_kw,
                    "q_kvar": q_kvar,
                }

    return {
        "source": "none",
        "timestamp": pd.NaT,
        "p_kw": None,
        "q_kvar": None,
    }


def build_power_flow_summary(result: dict[str, Any]) -> dict[str, Any]:
    results_tables = dict(result.get("results_tables", {}) or {})
    vm_series = _series_from_results(results_tables, "res_bus", "vm_pu")
    line_loading = _series_from_results(results_tables, "res_line", "loading_percent")

    min_voltage = _coerce_float(result.get("min_voltage_pu"))
    max_voltage = _coerce_float(result.get("max_voltage_pu"))
    if min_voltage is None and not vm_series.empty:
        min_voltage = _coerce_float(vm_series.min(skipna=True))
    if max_voltage is None and not vm_series.empty:
        max_voltage = _coerce_float(vm_series.max(skipna=True))

    max_line_loading = _coerce_float(result.get("max_line_loading_pct"))
    if max_line_loading is None and not line_loading.empty:
        max_line_loading = _coerce_float(line_loading.max(skipna=True))

    voltage_violations = result.get("num_voltage_violations")
    if voltage_violations is None:
        if vm_series.empty:
            voltage_violations = 0
        else:
            voltage_violations = int(((vm_series < GRID_MAP_VOLTAGE_MIN_PU) | (vm_series > GRID_MAP_VOLTAGE_MAX_PU)).sum())

    overloaded_lines = result.get("num_overloaded_lines")
    if overloaded_lines is None:
        overloaded_lines = int((line_loading > GRID_MAP_LINE_LOADING_LIMIT_PCT).sum()) if not line_loading.empty else 0

    return {
        "min_voltage_pu": min_voltage,
        "max_voltage_pu": max_voltage,
        "num_voltage_violations": int(voltage_violations or 0),
        "max_line_loading_pct": max_line_loading,
        "num_overloaded_lines": int(overloaded_lines or 0),
    }


def build_dynamic_payload(power_flow_result: dict[str, Any], topology_cache: dict[str, Any]) -> dict[str, Any]:
    results_tables = dict(power_flow_result.get("results_tables", {}) or {})
    res_bus = results_tables.get("res_bus")
    res_line = results_tables.get("res_line")

    bus_vm = (
        pd.to_numeric(res_bus["vm_pu"], errors="coerce")
        if isinstance(res_bus, pd.DataFrame) and "vm_pu" in res_bus.columns
        else pd.Series(dtype=float)
    )
    line_loading = (
        pd.to_numeric(res_line["loading_percent"], errors="coerce")
        if isinstance(res_line, pd.DataFrame) and "loading_percent" in res_line.columns
        else pd.Series(dtype=float)
    )

    bus_dynamic = {}
    for bus_index in topology_cache.get("bus_order", []):
        vm_pu = _coerce_float(bus_vm.get(bus_index))
        status = "unknown"
        if vm_pu is not None:
            if vm_pu < GRID_MAP_VOLTAGE_MIN_PU or vm_pu > GRID_MAP_VOLTAGE_MAX_PU:
                status = "violation"
            else:
                status = "ok"
        bus_dynamic[str(bus_index)] = {
            "vm_pu": vm_pu,
            "status": status,
            "hover": f"Bus {bus_index}<br>Voltage={vm_pu:.4f} pu" if vm_pu is not None else f"Bus {bus_index}<br>Voltage=n/a",
        }

    line_dynamic = {}
    for line_index in topology_cache.get("line_order", []):
        loading_pct = _coerce_float(line_loading.get(line_index))
        status = "unknown"
        if loading_pct is not None:
            status = "overloaded" if loading_pct > GRID_MAP_LINE_LOADING_LIMIT_PCT else "ok"
        line_dynamic[str(line_index)] = {
            "loading_pct": loading_pct,
            "status": status,
            "hover": (
                f"Line {line_index}<br>Loading={loading_pct:.1f}%"
                if loading_pct is not None
                else f"Line {line_index}<br>Loading=n/a"
            ),
        }

    return {
        "bus": bus_dynamic,
        "line": line_dynamic,
    }


def run_grid_map_power_flow(input_payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    simulator_module = _import_simulator_module()
    tz = get_config_tz(config)

    p_kw = _coerce_float(input_payload.get("p_kw"))
    q_kvar = _coerce_float(input_payload.get("q_kvar"))
    timestamp = normalize_timestamp_value(input_payload.get("timestamp"), tz)
    if p_kw is None or q_kvar is None or pd.isna(timestamp):
        raise ValueError("LIB power-flow inputs are incomplete.")

    p_mw = -float(p_kw) / 1000.0
    q_mvar = float(q_kvar) / 1000.0
    timestamp_iso = serialize_iso_with_tz(timestamp, tz=tz)

    result = simulator_module.run_power_flow(
        battery_p_mw=p_mw,
        battery_q_mvar=q_mvar,
        timestamp_iso=timestamp_iso,
    )
    return {
        "power_flow_result": result,
        "requested_timestamp_local": timestamp_iso,
        "battery_input_p_kw": float(p_kw),
        "battery_input_q_kvar": float(q_kvar),
        "battery_input_p_mw": float(p_mw),
        "battery_input_q_mvar": float(q_mvar),
    }


def publish_grid_map_topology(shared_data: dict[str, Any], *, topology_cache: dict[str, Any]) -> None:
    topology_meta = summarize_topology_cache(topology_cache)
    with shared_data["lock"]:
        runtime_state = shared_data.setdefault(GRID_MAP_STATUS_KEY, default_grid_map_runtime(5.0))
        runtime_state["topology_ready"] = True
        runtime_state["topology_error"] = None
        runtime_state["topology_cache"] = topology_cache
        runtime_state["topology_cache_meta"] = topology_meta
        runtime_state["coordinate_mode"] = str((topology_meta or {}).get("coordinate_mode") or "schematic")
        runtime_state["source_crs"] = (topology_meta or {}).get("source_crs")
        runtime_state["target_crs"] = (topology_meta or {}).get("target_crs")
        runtime_state["map_background_enabled"] = bool((topology_meta or {}).get("map_background_enabled", False))
        runtime_state["map_background_reason"] = (topology_meta or {}).get("map_background_reason")


def publish_grid_map_topology_error(shared_data: dict[str, Any], *, error_text: str) -> None:
    with shared_data["lock"]:
        runtime_state = shared_data.setdefault(GRID_MAP_STATUS_KEY, default_grid_map_runtime(5.0))
        runtime_state["state"] = "error"
        runtime_state["topology_ready"] = False
        runtime_state["topology_error"] = str(error_text)
        runtime_state["last_error"] = str(error_text)
        runtime_state["coordinate_mode"] = "schematic"
        runtime_state["source_crs"] = None
        runtime_state["target_crs"] = None
        runtime_state["map_background_enabled"] = False
        runtime_state["map_background_reason"] = "topology_error"
        runtime_state["stale"] = True


def _compute_stale_flag(last_success_at: Any, now_value: Any, poll_period_s: float) -> bool:
    success_ts = pd.Timestamp(last_success_at) if last_success_at is not None else pd.NaT
    now_ts = pd.Timestamp(now_value) if now_value is not None else pd.NaT
    if pd.isna(success_ts) or pd.isna(now_ts):
        return True
    try:
        return (now_ts - success_ts).total_seconds() > max(10.0, float(poll_period_s) * 2.5)
    except Exception:
        return True


def publish_grid_map_success(
    shared_data: dict[str, Any],
    *,
    now_value: Any,
    input_payload: dict[str, Any],
    run_payload: dict[str, Any],
    summary: dict[str, Any],
    dynamic_payload: dict[str, Any],
) -> None:
    pf_result = dict(run_payload.get("power_flow_result", {}) or {})
    selected_local = pf_result.get("selected_timestamp_local")
    selected_utc = pf_result.get("selected_timestamp_utc")
    requested_local = run_payload.get("requested_timestamp_local")
    with shared_data["lock"]:
        runtime_state = shared_data.setdefault(GRID_MAP_STATUS_KEY, default_grid_map_runtime(5.0))
        runtime_state["state"] = "ok"
        runtime_state["last_run_at"] = now_value
        runtime_state["last_success_at"] = now_value
        runtime_state["last_error"] = None
        runtime_state["requested_timestamp_local"] = requested_local
        runtime_state["selected_timestamp_local"] = selected_local
        runtime_state["selected_timestamp_utc"] = selected_utc
        runtime_state["used_previous_hour_fallback"] = bool(pf_result.get("used_previous_hour_fallback", False))
        runtime_state["input_source"] = str(input_payload.get("source") or "none")
        runtime_state["input_measured_at"] = input_payload.get("timestamp")
        runtime_state["battery_input_p_kw"] = run_payload.get("battery_input_p_kw")
        runtime_state["battery_input_q_kvar"] = run_payload.get("battery_input_q_kvar")
        runtime_state["battery_input_p_mw"] = run_payload.get("battery_input_p_mw")
        runtime_state["battery_input_q_mvar"] = run_payload.get("battery_input_q_mvar")
        runtime_state["summary"] = dict(summary or {})
        runtime_state["dynamic_payload"] = dict(dynamic_payload or {})
        runtime_state["stale"] = False


def publish_grid_map_error(
    shared_data: dict[str, Any],
    *,
    now_value: Any,
    error_text: str,
    input_payload: dict[str, Any] | None = None,
) -> None:
    with shared_data["lock"]:
        runtime_state = shared_data.setdefault(GRID_MAP_STATUS_KEY, default_grid_map_runtime(5.0))
        runtime_state["state"] = "error"
        runtime_state["last_run_at"] = now_value
        runtime_state["last_error"] = str(error_text)
        if isinstance(input_payload, dict):
            runtime_state["input_source"] = str(input_payload.get("source") or runtime_state.get("input_source") or "none")
            runtime_state["input_measured_at"] = input_payload.get("timestamp") or runtime_state.get("input_measured_at")
            runtime_state["battery_input_p_kw"] = input_payload.get("p_kw")
            runtime_state["battery_input_q_kvar"] = input_payload.get("q_kvar")
            p_kw = _coerce_float(input_payload.get("p_kw"))
            q_kvar = _coerce_float(input_payload.get("q_kvar"))
            runtime_state["battery_input_p_mw"] = None if p_kw is None else -float(p_kw) / 1000.0
            runtime_state["battery_input_q_mvar"] = None if q_kvar is None else float(q_kvar) / 1000.0
        runtime_state["stale"] = _compute_stale_flag(
            runtime_state.get("last_success_at"),
            now_value,
            runtime_state.get("poll_period_s", 5.0),
        )


def snapshot_grid_map_runtime(shared_data: dict[str, Any]) -> dict[str, Any]:
    with shared_data["lock"]:
        current = dict(shared_data.get(GRID_MAP_STATUS_KEY, {}) or {})
        topology_cache = current.get("topology_cache")
        # The topology cache is immutable after startup, so callbacks can safely
        # share the reference without paying a large deep-copy cost every refresh.
        current["topology_cache"] = topology_cache
        dynamic_payload = current.get("dynamic_payload")
        current["dynamic_payload"] = copy.deepcopy(dynamic_payload) if isinstance(dynamic_payload, dict) else dynamic_payload
        summary = current.get("summary")
        current["summary"] = dict(summary or {}) if isinstance(summary, dict) else summary
        meta = current.get("topology_cache_meta")
        current["topology_cache_meta"] = dict(meta or {}) if isinstance(meta, dict) else meta
        return current


def _voltage_color(vm_pu: float | None) -> str:
    if vm_pu is None:
        return "#9ca7a2"
    if vm_pu < GRID_MAP_VOLTAGE_MIN_PU or vm_pu > GRID_MAP_VOLTAGE_MAX_PU:
        return "#d93838"
    if vm_pu < 0.98 or vm_pu > 1.02:
        return "#d28c00"
    return "#00945a"


def _line_color(loading_pct: float | None) -> str:
    if loading_pct is None:
        return "#b2c4bc"
    if loading_pct > GRID_MAP_LINE_LOADING_LIMIT_PCT:
        return "#d93838"
    if loading_pct >= 80.0:
        return "#d28c00"
    return "#6d8f82"


def _line_width(loading_pct: float | None) -> float:
    if loading_pct is None:
        return 1.5
    return min(6.0, max(1.5, 1.5 + (float(loading_pct) / 50.0)))


def _format_metric(value: float | None, *, decimals: int, unit: str) -> str:
    if value is None:
        return f"n/a {unit}".strip()
    return f"{float(value):.{int(decimals)}f} {unit}".strip()


def _map_zoom_for_bounds(bounds: dict[str, float] | None) -> float:
    if not isinstance(bounds, dict):
        return 12.0
    lon_min = _coerce_float(bounds.get("x_min"))
    lon_max = _coerce_float(bounds.get("x_max"))
    lat_min = _coerce_float(bounds.get("y_min"))
    lat_max = _coerce_float(bounds.get("y_max"))
    if None in (lon_min, lon_max, lat_min, lat_max):
        return 12.0
    lon_span = max(1e-9, abs(lon_max - lon_min))
    lat_span = max(1e-9, abs(lat_max - lat_min))
    span = max(lon_span, lat_span)
    if span < 0.002:
        return 17.0
    if span < 0.005:
        return 16.0
    if span < 0.01:
        return 15.0
    if span < 0.02:
        return 14.0
    if span < 0.05:
        return 13.0
    if span < 0.1:
        return 12.0
    return 11.0


def _build_schematic_grid_map_figure(
    topology_cache: dict[str, Any],
    dynamic_payload: dict[str, Any],
    *,
    title: str,
    uirevision_key: str,
    plot_theme: dict[str, Any],
) -> go.Figure:
    fig = go.Figure()
    bounds = dict(topology_cache.get("bounds", {}) or {})
    bus_coords = dict(topology_cache.get("bus_coords", {}) or {})
    line_paths = dict(topology_cache.get("line_paths", {}) or {})
    trafo_paths = dict(topology_cache.get("trafo_paths", {}) or {})
    bus_dynamic = dict(dynamic_payload.get("bus", {}) or {})
    line_dynamic = dict(dynamic_payload.get("line", {}) or {})

    if not bus_coords:
        fig.add_annotation(text="Grid topology unavailable.", x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False)
        fig.update_layout(
            height=720,
            title=title,
            paper_bgcolor=plot_theme["paper_bg"],
            plot_bgcolor=plot_theme["paper_bg"],
            font=dict(color=plot_theme["text"], family=plot_theme["font_family"]),
            margin=dict(l=20, r=20, t=50, b=20),
            uirevision=uirevision_key,
        )
        fig.update_xaxes(visible=False)
        fig.update_yaxes(visible=False)
        return fig

    for line_index in topology_cache.get("line_order", []):
        path = line_paths.get(str(line_index)) or []
        if len(path) < 2:
            continue
        line_state = dict(line_dynamic.get(str(line_index), {}) or {})
        xs = [point[0] for point in path]
        ys = [point[1] for point in path]
        loading_pct = _coerce_float(line_state.get("loading_pct"))
        center_x, center_y = _line_center(path)
        hover_text = str(line_state.get("hover") or f"Line {line_index}")
        fig.add_trace(
            go.Scatter(
                x=xs,
                y=ys,
                mode="lines",
                line=dict(color=_line_color(loading_pct), width=_line_width(loading_pct)),
                hoverinfo="text",
                text=hover_text,
                name="Lines",
                showlegend=False,
            )
        )
        fig.add_trace(
            go.Scatter(
                x=[center_x],
                y=[center_y],
                mode="markers",
                marker=dict(size=12, color="rgba(0,0,0,0)"),
                hoverinfo="text",
                text=hover_text,
                showlegend=False,
            )
        )

    for trafo_index in topology_cache.get("trafo_order", []):
        path = trafo_paths.get(str(trafo_index)) or []
        if len(path) < 2:
            continue
        xs = [point[0] for point in path]
        ys = [point[1] for point in path]
        fig.add_trace(
            go.Scatter(
                x=xs,
                y=ys,
                mode="lines",
                line=dict(color="#2f5b4e", width=3, dash="dash"),
                hoverinfo="text",
                text=f"Transformer {trafo_index}",
                name="Transformers",
                showlegend=False,
            )
        )

    bus_x = []
    bus_y = []
    bus_color = []
    bus_text = []
    bus_size = []
    for bus_index in topology_cache.get("bus_order", []):
        point = bus_coords.get(str(bus_index))
        if not isinstance(point, list) or len(point) < 2:
            continue
        bus_state = dict(bus_dynamic.get(str(bus_index), {}) or {})
        vm_pu = _coerce_float(bus_state.get("vm_pu"))
        bus_x.append(point[0])
        bus_y.append(point[1])
        bus_color.append(_voltage_color(vm_pu))
        bus_text.append(str(bus_state.get("hover") or f"Bus {bus_index}"))
        bus_size.append(10 if bus_index == topology_cache.get("metadata", {}).get("battery_bus") else 7)

    fig.add_trace(
        go.Scatter(
            x=bus_x,
            y=bus_y,
            mode="markers",
            marker=dict(
                size=bus_size,
                color=bus_color,
                line=dict(color="#ffffff", width=1),
            ),
            hoverinfo="text",
            text=bus_text,
            name="Buses",
            showlegend=False,
        )
    )

    x_min = _coerce_float(bounds.get("x_min"))
    x_max = _coerce_float(bounds.get("x_max"))
    y_min = _coerce_float(bounds.get("y_min"))
    y_max = _coerce_float(bounds.get("y_max"))
    x_span = 1.0 if x_min is None or x_max is None else max(1e-9, x_max - x_min)
    y_span = 1.0 if y_min is None or y_max is None else max(1e-9, y_max - y_min)
    x_pad = x_span * 0.05
    y_pad = y_span * 0.05

    fig.update_layout(
        title=title,
        height=720,
        margin=dict(l=20, r=20, t=50, b=20),
        paper_bgcolor=plot_theme["paper_bg"],
        plot_bgcolor="#f8fcfa",
        font=dict(color=plot_theme["text"], family=plot_theme["font_family"], size=12),
        uirevision=uirevision_key,
    )
    fig.update_xaxes(
        visible=False,
        showgrid=False,
        zeroline=False,
        range=None if x_min is None or x_max is None else [x_min - x_pad, x_max + x_pad],
    )
    fig.update_yaxes(
        visible=False,
        showgrid=False,
        zeroline=False,
        scaleanchor="x",
        scaleratio=1,
        range=None if y_min is None or y_max is None else [y_min - y_pad, y_max + y_pad],
    )
    return fig


def _build_geographic_grid_map_figure(
    topology_cache: dict[str, Any],
    dynamic_payload: dict[str, Any],
    *,
    title: str,
    uirevision_key: str,
    plot_theme: dict[str, Any],
) -> go.Figure:
    fig = go.Figure()
    bounds = dict(topology_cache.get("geographic_bounds", {}) or {})
    center = dict(topology_cache.get("geographic_center", {}) or {})
    bus_coords = dict(topology_cache.get("geographic_bus_coords", {}) or {})
    line_paths = dict(topology_cache.get("geographic_line_paths", {}) or {})
    trafo_paths = dict(topology_cache.get("geographic_trafo_paths", {}) or {})
    bus_dynamic = dict(dynamic_payload.get("bus", {}) or {})
    line_dynamic = dict(dynamic_payload.get("line", {}) or {})

    if not bus_coords:
        return _build_schematic_grid_map_figure(
            topology_cache,
            dynamic_payload,
            title=title,
            uirevision_key=uirevision_key,
            plot_theme=plot_theme,
        )

    for line_index in topology_cache.get("line_order", []):
        path = line_paths.get(str(line_index)) or []
        if len(path) < 2:
            continue
        line_state = dict(line_dynamic.get(str(line_index), {}) or {})
        loading_pct = _coerce_float(line_state.get("loading_pct"))
        hover_text = str(line_state.get("hover") or f"Line {line_index}")
        fig.add_trace(
            go.Scattermap(
                lon=[point[0] for point in path],
                lat=[point[1] for point in path],
                mode="lines",
                line=dict(color=_line_color(loading_pct), width=_line_width(loading_pct)),
                hoverinfo="text",
                text=hover_text,
                name="Lines",
                showlegend=False,
            )
        )

    for trafo_index in topology_cache.get("trafo_order", []):
        path = trafo_paths.get(str(trafo_index)) or []
        if len(path) < 2:
            continue
        fig.add_trace(
            go.Scattermap(
                lon=[point[0] for point in path],
                lat=[point[1] for point in path],
                mode="lines",
                line=dict(color="#2f5b4e", width=3),
                hoverinfo="text",
                text=f"Transformer {trafo_index}",
                name="Transformers",
                showlegend=False,
            )
        )

    bus_lon = []
    bus_lat = []
    bus_color = []
    bus_text = []
    bus_size = []
    for bus_index in topology_cache.get("bus_order", []):
        point = bus_coords.get(str(bus_index))
        if not isinstance(point, list) or len(point) < 2:
            continue
        bus_state = dict(bus_dynamic.get(str(bus_index), {}) or {})
        vm_pu = _coerce_float(bus_state.get("vm_pu"))
        bus_lon.append(point[0])
        bus_lat.append(point[1])
        bus_color.append(_voltage_color(vm_pu))
        bus_text.append(str(bus_state.get("hover") or f"Bus {bus_index}"))
        bus_size.append(14 if bus_index == topology_cache.get("metadata", {}).get("battery_bus") else 10)

    fig.add_trace(
        go.Scattermap(
            lon=bus_lon,
            lat=bus_lat,
            mode="markers",
            marker=dict(
                size=bus_size,
                color=bus_color,
                opacity=0.95,
            ),
            hoverinfo="text",
            text=bus_text,
            name="Buses",
            showlegend=False,
        )
    )

    map_center = {
        "lon": _coerce_float(center.get("lon")) or 0.0,
        "lat": _coerce_float(center.get("lat")) or 0.0,
    }
    fig.update_layout(
        title=title,
        height=720,
        margin=dict(l=20, r=20, t=50, b=20),
        paper_bgcolor=plot_theme["paper_bg"],
        font=dict(color=plot_theme["text"], family=plot_theme["font_family"], size=12),
        uirevision=uirevision_key,
        map=dict(
            style=GRID_MAP_MAP_STYLE,
            center=map_center,
            zoom=_map_zoom_for_bounds(bounds),
        ),
    )
    return fig


def build_grid_map_figure(
    topology_cache: dict[str, Any] | None,
    dynamic_payload: dict[str, Any] | None,
    *,
    title: str = "Distribution Grid Map",
    uirevision_key: str = "grid-map",
) -> go.Figure:
    plot_theme = dict(DEFAULT_PLOT_THEME)
    dynamic_payload = dict(dynamic_payload or {})
    topology_cache = dict(topology_cache or {})
    if bool(topology_cache.get("map_background_enabled", False)) and topology_cache.get("geographic_bus_coords"):
        return _build_geographic_grid_map_figure(
            topology_cache,
            dynamic_payload,
            title=title,
            uirevision_key=uirevision_key,
            plot_theme=plot_theme,
        )
    return _build_schematic_grid_map_figure(
        topology_cache,
        dynamic_payload,
        title=title,
        uirevision_key=uirevision_key,
        plot_theme=plot_theme,
    )


def build_grid_map_meta_lines(runtime_state: dict[str, Any], config: dict[str, Any]) -> list[str]:
    tz = get_config_tz(config)

    def _format_ts(value: Any) -> str:
        if value is None:
            return "never"
        ts = normalize_timestamp_value(value, tz)
        if pd.isna(ts):
            return "never"
        return ts.strftime("%Y-%m-%d %H:%M:%S %Z")

    last_success = _format_ts(runtime_state.get("last_success_at"))
    selected = runtime_state.get("selected_timestamp_local") or "n/a"
    requested = runtime_state.get("requested_timestamp_local") or "n/a"
    source = str(runtime_state.get("input_source") or "none")
    stale = bool(runtime_state.get("stale", True))
    fallback = bool(runtime_state.get("used_previous_hour_fallback", False))
    coordinate_mode = str(runtime_state.get("coordinate_mode") or "schematic")
    source_crs = runtime_state.get("source_crs") or "n/a"
    target_crs = runtime_state.get("target_crs") or "n/a"
    map_background_enabled = bool(runtime_state.get("map_background_enabled", False))
    map_background_reason = str(runtime_state.get("map_background_reason") or "").strip()
    input_p = _coerce_float(runtime_state.get("battery_input_p_kw"))
    input_q = _coerce_float(runtime_state.get("battery_input_q_kvar"))
    error_text = str(runtime_state.get("last_error") or "").strip()

    lines = [
        f"Last Success: {last_success} | Input Source: {source} | Stale: {stale}",
        (
            f"Map Mode: {coordinate_mode} | Background: {map_background_enabled} | "
            f"CRS: {source_crs} -> {target_crs}"
        ),
        (
            f"Simulation Timestamp: requested={requested} | selected={selected} | "
            f"previous-hour fallback={fallback}"
        ),
        (
            f"LIB Input: P={_format_metric(input_p, decimals=1, unit='kW')} | "
            f"Q={_format_metric(input_q, decimals=1, unit='kvar')}"
        ),
    ]
    if map_background_reason:
        lines.append(f"Map Background Reason: {map_background_reason}")
    if error_text:
        lines.append(f"Last Error: {error_text}")
    return lines
