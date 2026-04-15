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
import re
from typing import Any

import pandas as pd
import plotly.graph_objects as go
try:
    from dash import Patch
except Exception:  # pragma: no cover - fallback for lightweight non-dashboard environments
    Patch = None

from dashboard.plotting import DEFAULT_PLOT_THEME
from modbus.client import ModbusClient
from modbus.codec import write_point_internal
from runtime.contracts import resolve_grid_map_voltage_write_endpoint
from time_utils import get_config_tz, normalize_datetime_series, normalize_timestamp_value, serialize_iso_with_tz

GRID_MAP_VOLTAGE_MIN_PU = 0.95
GRID_MAP_VOLTAGE_MAX_PU = 1.05
GRID_MAP_LINE_LOADING_LIMIT_PCT = 100.0
GRID_MAP_VOLTAGE_COLOR_RED = "#c83b3b"
GRID_MAP_VOLTAGE_COLOR_AMBER = "#d97a1f"
GRID_MAP_VOLTAGE_COLOR_YELLOW_GREEN = "#d7b62a"
GRID_MAP_VOLTAGE_COLOR_GREEN = "#96cc56"
GRID_MAP_VOLTAGE_COLOR_DARK_CYAN_GREEN = "#2e8f85"
GRID_MAP_VOLTAGE_COLOR_LIGHT_BLUE_GREEN = "#5d97c9"
GRID_MAP_VOLTAGE_COLOR_BLUE = "#446fbe"
GRID_MAP_VOLTAGE_COLOR_MISSING = "#9ca7a2"
GRID_MAP_STATUS_KEY = "grid_map_runtime"
GRID_MAP_SOURCE_CRS = "EPSG:32630"
GRID_MAP_TARGET_CRS = "EPSG:4326"
GRID_MAP_BACKGROUND_MODE_NONE = "none"
GRID_MAP_BACKGROUND_MODE_STREET = "street"
GRID_MAP_BACKGROUND_MODE_SATELLITE = "satellite"
GRID_MAP_DEFAULT_BACKGROUND_MODE = GRID_MAP_BACKGROUND_MODE_STREET
GRID_MAP_SATELLITE_STYLE = {
    "version": 8,
    "name": "orto",
    "metadata": {},
    "center": [1.537786, 41.837539],
    "zoom": 12,
    "bearing": 0,
    "pitch": 0,
    "light": {
        "anchor": "viewport",
        "color": "white",
        "intensity": 0.4,
        "position": [1.15, 45, 30],
    },
    "sources": {
        "ortoEsri": {
            "type": "raster",
            "tiles": [
                "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
            ],
            "tileSize": 256,
            "maxzoom": 18,
            "attribution": "ESRI &copy; <a href='http://www.esri.com'>ESRI</a>",
        },
        "ortoInstaMaps": {
            "type": "raster",
            "tiles": ["https://tilemaps.icgc.cat/mapfactory/wmts/orto_8_12/CAT3857/{z}/{x}/{y}.png"],
            "tileSize": 256,
            "maxzoom": 13,
        },
        "ortoICGC": {
            "type": "raster",
            "tiles": ["https://geoserveis.icgc.cat/icc_mapesmultibase/noutm/wmts/orto/GRID3857/{z}/{x}/{y}.jpeg"],
            "tileSize": 256,
            "minzoom": 13.1,
            "maxzoom": 20,
        },
        "openmaptiles": {
            "type": "vector",
            "url": "https://geoserveis.icgc.cat/contextmaps/basemap.json",
        },
    },
    "sprite": "https://geoserveis.icgc.cat/contextmaps/sprites/sprite@1",
    "glyphs": "https://geoserveis.icgc.cat/contextmaps/glyphs/{fontstack}/{range}.pbf",
    "layers": [
        {
            "id": "background",
            "type": "background",
            "paint": {"background-color": "#F4F9F4"},
        },
        {
            "id": "ortoEsri",
            "type": "raster",
            "source": "ortoEsri",
            "layout": {"visibility": "visible"},
        },
        {
            "id": "ortoICGC",
            "type": "raster",
            "source": "ortoICGC",
            "minzoom": 13.1,
            "layout": {"visibility": "visible"},
        },
        {
            "id": "ortoInstaMaps",
            "type": "raster",
            "source": "ortoInstaMaps",
            "layout": {"visibility": "visible"},
        },
    ],
}
GRID_MAP_BACKGROUND_STYLE_BY_MODE = {
    GRID_MAP_BACKGROUND_MODE_NONE: "white-bg",
    GRID_MAP_BACKGROUND_MODE_STREET: "open-street-map",
    GRID_MAP_BACKGROUND_MODE_SATELLITE: GRID_MAP_SATELLITE_STYLE,
}
GRID_MAP_MAP_STYLE = GRID_MAP_BACKGROUND_STYLE_BY_MODE[GRID_MAP_DEFAULT_BACKGROUND_MODE]
GRID_MAP_STARTUP_FIT_PADDING_PX = 32
GRID_MAP_FALLBACK_VIEWPORT_WIDTH_PX = 960
GRID_MAP_FALLBACK_VIEWPORT_HEIGHT_PX = 680
GRID_MAP_MIN_ZOOM = 0.0
GRID_MAP_MAX_ZOOM = 24.0
GRID_MAP_DEGENERATE_BOUNDS_ZOOM = 20.0
GRID_MAP_LINE_WARNING_LIMIT_PCT = 80.0
GRID_MAP_LINE_HOVER_MARKER_SIZE = 16
GRID_MAP_LINE_HOVER_MARKER_COLOR = "rgba(47,91,78,0.12)"

GRID_MAP_TRACE_ROLE_LINE_NORMAL = "line_normal"
GRID_MAP_TRACE_ROLE_LINE_WARNING = "line_warning"
GRID_MAP_TRACE_ROLE_LINE_OVERLOADED = "line_overloaded"
GRID_MAP_TRACE_ROLE_TRAFO = "trafo"
GRID_MAP_TRACE_ROLE_LINE_HOVER = "line_hover"
GRID_MAP_TRACE_ROLE_BUS = "bus"
GRID_MAP_SCENARIO_WITH_BATTERY = "with_battery"
GRID_MAP_SCENARIO_WITHOUT_BATTERY = "without_battery"

GRID_MAP_LINE_BUCKETS = ("normal", "warning", "overloaded")
GRID_MAP_VOLTAGE_BUCKET_SPECS = (
    ("grid_map_voltage_bucket_lt_0_925_count", None, 0.925),
    ("grid_map_voltage_bucket_0_925_to_0_95_count", 0.925, 0.95),
    ("grid_map_voltage_bucket_0_95_to_0_975_count", 0.95, 0.975),
    ("grid_map_voltage_bucket_0_975_to_1_025_count", 0.975, 1.025),
    ("grid_map_voltage_bucket_1_025_to_1_05_count", 1.025, 1.05),
    ("grid_map_voltage_bucket_1_05_to_1_075_count", 1.05, 1.075),
    ("grid_map_voltage_bucket_gte_1_075_count", 1.075, None),
)

_SIMULATOR_MODULE = None


def default_grid_map_scenario_result() -> dict[str, Any]:
    return {
        "requested_timestamp_local": None,
        "selected_timestamp_local": None,
        "selected_timestamp_utc": None,
        "used_previous_hour_fallback": False,
        "battery_input_p_kw": None,
        "battery_input_q_kvar": None,
        "battery_input_p_mw": None,
        "battery_input_q_mvar": None,
        "summary": None,
        "dynamic_payload": None,
    }


def _normalize_grid_map_scenario_results(value: Any) -> dict[str, dict[str, Any]]:
    provided = dict(value or {}) if isinstance(value, dict) else {}
    normalized = {}
    for scenario_key in (GRID_MAP_SCENARIO_WITH_BATTERY, GRID_MAP_SCENARIO_WITHOUT_BATTERY):
        merged = default_grid_map_scenario_result()
        current = dict(provided.get(scenario_key) or {}) if isinstance(provided.get(scenario_key), dict) else {}
        merged.update(current)
        normalized[scenario_key] = merged
    return normalized


def _normalize_background_mode(value: Any) -> str:
    normalized = str(value or GRID_MAP_DEFAULT_BACKGROUND_MODE).strip().lower()
    if normalized in GRID_MAP_BACKGROUND_STYLE_BY_MODE:
        return normalized
    return GRID_MAP_DEFAULT_BACKGROUND_MODE


def _requested_background_mode_from_config(config: dict[str, Any] | None) -> str:
    if isinstance(config, dict):
        return _normalize_background_mode(config.get("GRID_MAP_BACKGROUND_MODE"))
    return GRID_MAP_DEFAULT_BACKGROUND_MODE


def _map_style_for_background_mode(background_mode: Any) -> Any:
    normalized_mode = _normalize_background_mode(background_mode)
    style = GRID_MAP_BACKGROUND_STYLE_BY_MODE[normalized_mode]
    if isinstance(style, dict):
        return copy.deepcopy(style)
    return style


def _render_on_map(topology_cache: dict[str, Any]) -> bool:
    return str(topology_cache.get("coordinate_mode") or "schematic") == "geographic"


def _clip_latitude_for_mercator(lat: Any) -> float | None:
    latitude = _coerce_float(lat)
    if latitude is None:
        return None
    return float(min(85.05112878, max(-85.05112878, latitude)))


def _mercator_world_y_fraction(lat: Any) -> float | None:
    latitude = _clip_latitude_for_mercator(lat)
    if latitude is None:
        return None
    sine = min(max(math.sin(math.radians(latitude)), -0.9999), 0.9999)
    return float(0.5 - (math.log((1.0 + sine) / (1.0 - sine)) / (4.0 * math.pi)))


def _grid_map_fit_meta(topology_cache: dict[str, Any] | None, topology_revision: Any) -> dict[str, Any]:
    topology_cache = dict(topology_cache or {})
    meta = {
        "grid_map_coordinate_mode": str(topology_cache.get("coordinate_mode") or "schematic"),
        "grid_map_topology_revision": topology_revision,
    }
    if _render_on_map(topology_cache):
        bounds = dict(topology_cache.get("geographic_bounds", {}) or {})
        west = _coerce_float(bounds.get("x_min"))
        east = _coerce_float(bounds.get("x_max"))
        south = _coerce_float(bounds.get("y_min"))
        north = _coerce_float(bounds.get("y_max"))
        if None not in (west, east, south, north):
            meta["grid_map_fit_bounds"] = {
                "west": float(west),
                "east": float(east),
                "south": float(south),
                "north": float(north),
            }
            meta["grid_map_fit_padding_px"] = GRID_MAP_STARTUP_FIT_PADDING_PX
    return meta


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
        "scenario_results": _normalize_grid_map_scenario_results(None),
        "initial_figure": None,
        "trace_index_meta": None,
        "topology_revision": None,
        "dynamic_revision": 0,
        "coordinate_mode": "schematic",
        "source_crs": None,
        "target_crs": None,
        "map_background_mode": GRID_MAP_BACKGROUND_MODE_NONE,
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
            merged["scenario_results"] = _normalize_grid_map_scenario_results(merged.get("scenario_results"))
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


def _derive_battery_bus_voltage_kv(
    simulator_module: Any,
    power_flow_result: dict[str, Any],
) -> float | None:
    voltage_kv = _coerce_float((power_flow_result or {}).get("battery_bus_vm_kv"))
    if voltage_kv is not None:
        return voltage_kv

    assets = None
    try:
        if hasattr(simulator_module, "_load_assets"):
            loaded_assets = simulator_module._load_assets()
            if isinstance(loaded_assets, dict):
                assets = loaded_assets

        metadata = {}
        if hasattr(simulator_module, "get_metadata"):
            loaded_metadata = simulator_module.get_metadata()
            if isinstance(loaded_metadata, dict):
                metadata = dict(loaded_metadata)
        elif isinstance(assets, dict):
            metadata = dict((assets.get("metadata") or {}))

        battery_bus_raw = metadata.get("battery_bus")
        if battery_bus_raw is None:
            return None
        battery_bus = int(battery_bus_raw)

        battery_bus_vm_pu = _coerce_float((power_flow_result or {}).get("battery_bus_vm_pu"))
        if battery_bus_vm_pu is None:
            results_tables = dict((power_flow_result or {}).get("results_tables", {}) or {})
            vm_series = _series_from_results(results_tables, "res_bus", "vm_pu")
            battery_bus_vm_pu = _coerce_float(vm_series.get(battery_bus))
        if battery_bus_vm_pu is None:
            return None

        base_net = None
        if hasattr(simulator_module, "get_base_network_copy"):
            base_net = simulator_module.get_base_network_copy()
        elif isinstance(assets, dict):
            base_net = assets.get("base_net")

        bus_table = getattr(base_net, "bus", None)
        if not isinstance(bus_table, pd.DataFrame) or battery_bus not in bus_table.index or "vn_kv" not in bus_table.columns:
            return None

        nominal_kv = _coerce_float(bus_table.at[battery_bus, "vn_kv"])
        if nominal_kv is None:
            return None

        return float(battery_bus_vm_pu) * float(nominal_kv)
    except Exception as exc:
        logging.debug("Grid map: failed to derive battery_bus_vm_kv from simulator assets: %s", exc)
        return None


def _normalize_power_flow_result(
    simulator_module: Any,
    power_flow_result: dict[str, Any],
) -> dict[str, Any]:
    normalized = dict(power_flow_result or {})
    battery_bus_vm_kv = _derive_battery_bus_voltage_kv(simulator_module, normalized)
    if battery_bus_vm_kv is not None:
        normalized["battery_bus_vm_kv"] = battery_bus_vm_kv
    return normalized


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
    if not isinstance(getattr(net, "bus_geodata", None), pd.DataFrame):
        net.bus_geodata = pd.DataFrame(columns=["x", "y"])
    if not isinstance(getattr(net, "line_geodata", None), pd.DataFrame):
        net.line_geodata = pd.DataFrame(columns=["coords"])
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
    if len(path) == 1:
        point = path[0]
        return float(point[0]), float(point[1])

    total_length = 0.0
    segments: list[tuple[tuple[float, float], tuple[float, float], float]] = []
    previous_point = path[0]
    for current_point in path[1:]:
        dx = float(current_point[0]) - float(previous_point[0])
        dy = float(current_point[1]) - float(previous_point[1])
        segment_length = math.hypot(dx, dy)
        segments.append((previous_point, current_point, segment_length))
        total_length += segment_length
        previous_point = current_point

    if total_length <= 0.0:
        xs = [float(point[0]) for point in path]
        ys = [float(point[1]) for point in path]
        return float(sum(xs) / len(xs)), float(sum(ys) / len(ys))

    target_length = total_length / 2.0
    traversed_length = 0.0
    for start_point, end_point, segment_length in segments:
        if traversed_length + segment_length < target_length:
            traversed_length += segment_length
            continue
        if segment_length <= 0.0:
            return float(end_point[0]), float(end_point[1])
        ratio = (target_length - traversed_length) / segment_length
        x = float(start_point[0]) + (float(end_point[0]) - float(start_point[0])) * ratio
        y = float(start_point[1]) + (float(end_point[1]) - float(start_point[1])) * ratio
        return float(x), float(y)

    last_point = path[-1]
    return float(last_point[0]), float(last_point[1])


def _prepare_plot_net_for_pandapower_traces(plot_net: Any) -> Any:
    prepared_net = copy.deepcopy(plot_net)
    _normalize_geojson_components_for_convert_crs(prepared_net)
    return prepared_net


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


def _geojson_point(point: tuple[float, float]) -> str:
    return json.dumps({"type": "Point", "coordinates": [float(point[0]), float(point[1])]})


def _geojson_linestring(path: list[tuple[float, float]]) -> str:
    return json.dumps({"type": "LineString", "coordinates": [[float(x), float(y)] for x, y in list(path or [])]})


def _prepare_plot_net(
    net: Any,
    *,
    bus_coords: dict[int, tuple[float, float]],
    line_paths: dict[int, list[tuple[float, float]]],
) -> Any:
    plot_net = copy.deepcopy(net)

    if isinstance(getattr(plot_net, "bus", None), pd.DataFrame):
        plot_net.bus = plot_net.bus.copy()
        plot_net.bus["geo"] = pd.NA
        for bus_index, point in bus_coords.items():
            if bus_index in plot_net.bus.index:
                plot_net.bus.at[bus_index, "geo"] = _geojson_point(point)
        plot_net.bus_geodata = pd.DataFrame(
            [{"x": float(point[0]), "y": float(point[1])} for _, point in sorted(bus_coords.items())],
            index=[int(index) for index in sorted(bus_coords.keys())],
        )

    if isinstance(getattr(plot_net, "line", None), pd.DataFrame):
        plot_net.line = plot_net.line.copy()
        plot_net.line["geo"] = pd.NA
        for line_index, path in line_paths.items():
            if line_index in plot_net.line.index and len(path) >= 2:
                plot_net.line.at[line_index, "geo"] = _geojson_linestring(path)
        plot_net.line_geodata = pd.DataFrame(
            [{"coords": [(float(point[0]), float(point[1])) for point in path]} for _, path in sorted(line_paths.items()) if len(path) >= 2],
            index=[int(index) for index, path in sorted(line_paths.items()) if len(path) >= 2],
        )

    return plot_net


def _trace_index_meta_from_data(trace_data: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    meta = []
    for index, trace in enumerate(list(trace_data or [])):
        trace_dict = dict(trace or {})
        name = str(trace_dict.get("name") or "")
        mode = str(trace_dict.get("mode") or "")
        text_value = trace_dict.get("text")
        if isinstance(text_value, str):
            text_str = text_value
        elif text_value is None:
            text_str = ""
        else:
            try:
                text_str = " ".join(str(item) for item in list(text_value))
            except TypeError:
                text_str = str(text_value)
        role = None
        element_index = None
        if name == "Lines" and mode == "lines":
            role = "line"
            match = re.search(r"Line\s+(\d+)", text_str)
            if match:
                element_index = int(match.group(1))
        elif name == "Transformers" and mode == "lines":
            role = "trafo"
            match = re.search(r"Transformer\s+(\d+)", text_str)
            if match:
                element_index = int(match.group(1))
        elif name == "Buses" and mode == "markers":
            role = "bus"
        elif mode == "markers" and (name == "edge_center" or name.endswith("-center")):
            if "Transformer" in text_str:
                role = "trafo_hover"
            else:
                role = "line_hover"
        meta.append(
            {
                "index": int(index),
                "type": str(trace_dict.get("type") or ""),
                "name": name,
                "mode": mode,
                "role": role,
                "element_index": element_index,
            }
        )
    return meta


def _default_dynamic_payload_for_topology(topology_cache: dict[str, Any]) -> dict[str, Any]:
    _ = topology_cache
    return {"bus": {}, "line": {}, "trafo": {}}


def _trace_role_groups(
    topology_cache: dict[str, Any],
    trace_index_meta: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    line_trace_by_element = {}
    trafo_trace_by_element = {}
    line_hover_trace_indices = []
    trafo_hover_trace_indices = []
    bus_trace_index = None

    for item in list(trace_index_meta or []):
        trace_index = int(item.get("index", -1))
        if trace_index < 0:
            continue
        role = str(item.get("role") or "")
        element_index = item.get("element_index")
        if role == "line" and element_index is not None:
            line_trace_by_element[int(element_index)] = trace_index
        elif role == "trafo" and element_index is not None:
            trafo_trace_by_element[int(element_index)] = trace_index
        elif role == "line_hover":
            line_hover_trace_indices.append(trace_index)
        elif role == "trafo_hover":
            trafo_hover_trace_indices.append(trace_index)
        elif role == "bus":
            bus_trace_index = trace_index

    return {
        "line_trace_indices": [
            int(line_trace_by_element[element_index])
            for element_index in list(topology_cache.get("line_order", []) or [])
            if int(element_index) in line_trace_by_element
        ],
        "line_hover_trace_indices": [int(index) for index in line_hover_trace_indices],
        "trafo_trace_indices": [
            int(trafo_trace_by_element[element_index])
            for element_index in list(topology_cache.get("trafo_order", []) or [])
            if int(element_index) in trafo_trace_by_element
        ],
        "trafo_hover_trace_indices": [int(index) for index in trafo_hover_trace_indices],
        "bus_trace_index": None if bus_trace_index is None else int(bus_trace_index),
    }


def build_grid_map_live_style_payload(
    topology_cache: dict[str, Any] | None,
    trace_index_meta: list[dict[str, Any]] | None,
    dynamic_payload: dict[str, Any] | None,
    *,
    topology_revision: Any,
    dynamic_revision: int,
) -> dict[str, Any] | None:
    if not isinstance(topology_cache, dict) or not isinstance(trace_index_meta, list) or not trace_index_meta:
        return None

    dynamic_payload = dict(dynamic_payload or {})
    bus_dynamic = dict(dynamic_payload.get("bus", {}) or {})
    line_dynamic = dict(dynamic_payload.get("line", {}) or {})
    trafo_dynamic = dict(dynamic_payload.get("trafo", {}) or {})
    groups = _trace_role_groups(topology_cache, trace_index_meta)

    bus_colors = []
    bus_text = []
    for bus_index in list(topology_cache.get("bus_order", []) or []):
        bus_state = dict(bus_dynamic.get(str(bus_index), {}) or {})
        vm_pu = _coerce_float(bus_state.get("vm_pu"))
        bus_colors.append(_voltage_color(vm_pu))
        bus_text.append(str(bus_state.get("hover") or f"Bus {bus_index}<br>Voltage=n/a"))

    line_colors = []
    line_text = []
    line_hover_text = []
    for line_index in list(topology_cache.get("line_order", []) or []):
        line_state = dict(line_dynamic.get(str(line_index), {}) or {})
        line_colors.append(_line_color(_coerce_float(line_state.get("loading_pct"))))
        hover_text = str(line_state.get("hover") or f"Line {line_index}<br>Loading=n/a")
        line_text.append(hover_text)
        line_hover_text.append(hover_text)

    trafo_colors = []
    trafo_text = []
    trafo_hover_text = []
    for trafo_index in list(topology_cache.get("trafo_order", []) or []):
        trafo_state = dict(trafo_dynamic.get(str(trafo_index), {}) or {})
        trafo_colors.append(_line_color(_coerce_float(trafo_state.get("loading_pct"))))
        hover_text = str(trafo_state.get("hover") or f"Transformer {trafo_index}<br>Loading=n/a")
        trafo_text.append(hover_text)
        trafo_hover_text.append(hover_text)

    return {
        "topology_revision": topology_revision,
        "dynamic_revision": int(dynamic_revision or 0),
        "line_trace_indices": list(groups.get("line_trace_indices", []) or []),
        "line_colors": list(line_colors),
        "line_text": list(line_text),
        "line_hover_trace_indices": list(groups.get("line_hover_trace_indices", []) or []),
        "line_hover_text_batches": [list(line_hover_text) for _ in list(groups.get("line_hover_trace_indices", []) or [])],
        "trafo_trace_indices": list(groups.get("trafo_trace_indices", []) or []),
        "trafo_colors": list(trafo_colors),
        "trafo_text": list(trafo_text),
        "trafo_hover_trace_indices": list(groups.get("trafo_hover_trace_indices", []) or []),
        "trafo_hover_text_batches": [list(trafo_hover_text) for _ in list(groups.get("trafo_hover_trace_indices", []) or [])],
        "bus_trace_index": groups.get("bus_trace_index"),
        "bus_colors": list(bus_colors),
        "bus_text": list(bus_text),
    }


def _apply_dynamic_payload_to_figure_dict(
    figure_dict: dict[str, Any] | None,
    topology_cache: dict[str, Any],
    trace_index_meta: list[dict[str, Any]] | None,
    dynamic_payload: dict[str, Any] | None,
    *,
    topology_revision: Any,
    dynamic_revision: int,
    title: str,
    uirevision_key: str,
) -> dict[str, Any]:
    figure_dict = copy.deepcopy(figure_dict) if isinstance(figure_dict, dict) else {"data": [], "layout": {}}
    data = list(figure_dict.get("data", []) or [])
    layout = dict(figure_dict.get("layout", {}) or {})

    dynamic_payload = dict(dynamic_payload or {})
    bus_dynamic = dict(dynamic_payload.get("bus", {}) or {})
    line_dynamic = dict(dynamic_payload.get("line", {}) or {})
    trafo_dynamic = dict(dynamic_payload.get("trafo", {}) or {})

    bus_colors = []
    bus_text = []
    for bus_index in list(topology_cache.get("bus_order", []) or []):
        bus_state = dict(bus_dynamic.get(str(bus_index), {}) or {})
        bus_colors.append(_voltage_color(_coerce_float(bus_state.get("vm_pu"))))
        bus_text.append(str(bus_state.get("hover") or f"Bus {bus_index}<br>Voltage=n/a"))

    line_text_by_index = {}
    for line_index in list(topology_cache.get("line_order", []) or []):
        line_state = dict(line_dynamic.get(str(line_index), {}) or {})
        line_text_by_index[int(line_index)] = str(line_state.get("hover") or f"Line {line_index}<br>Loading=n/a")

    trafo_text_by_index = {}
    for trafo_index in list(topology_cache.get("trafo_order", []) or []):
        trafo_state = dict(trafo_dynamic.get(str(trafo_index), {}) or {})
        trafo_text_by_index[int(trafo_index)] = str(
            trafo_state.get("hover") or f"Transformer {trafo_index}<br>Loading=n/a"
        )

    line_hover_text = [line_text_by_index[int(line_index)] for line_index in list(topology_cache.get("line_order", []) or [])]
    trafo_hover_text = [
        trafo_text_by_index[int(trafo_index)] for trafo_index in list(topology_cache.get("trafo_order", []) or [])
    ]

    for item in list(trace_index_meta or []):
        trace_index = int(item.get("index", -1))
        if trace_index < 0 or trace_index >= len(data):
            continue
        trace = dict(data[trace_index] or {})
        role = str(item.get("role") or "")
        element_index = item.get("element_index")
        if role == "line" and element_index is not None:
            line = dict(trace.get("line", {}) or {})
            line_state = dict(line_dynamic.get(str(int(element_index)), {}) or {})
            line["color"] = _line_color(_coerce_float(line_state.get("loading_pct")))
            trace["line"] = line
            trace["text"] = line_text_by_index.get(int(element_index), f"Line {int(element_index)}<br>Loading=n/a")
        elif role == "trafo" and element_index is not None:
            line = dict(trace.get("line", {}) or {})
            trafo_state = dict(trafo_dynamic.get(str(int(element_index)), {}) or {})
            line["color"] = _line_color(_coerce_float(trafo_state.get("loading_pct")))
            trace["line"] = line
            trace["text"] = trafo_text_by_index.get(
                int(element_index),
                f"Transformer {int(element_index)}<br>Loading=n/a",
            )
        elif role == "line_hover":
            trace["text"] = list(line_hover_text)
        elif role == "trafo_hover":
            trace["text"] = list(trafo_hover_text)
        elif role == "bus":
            marker = dict(trace.get("marker", {}) or {})
            marker["color"] = list(bus_colors)
            trace["marker"] = marker
            trace["text"] = list(bus_text)
        data[trace_index] = trace

    layout["title"] = {"text": title}
    layout["uirevision"] = uirevision_key
    layout["meta"] = {
        "grid_map_topology_revision": topology_revision,
        "grid_map_dynamic_revision": int(dynamic_revision or 0),
    }
    figure_dict["data"] = data
    figure_dict["layout"] = layout
    return figure_dict


def _build_low_trace_figure_patch(
    topology_cache: dict[str, Any],
    dynamic_payload: dict[str, Any] | None,
    *,
    dynamic_revision: int,
):
    if Patch is None:
        return None

    trace_roles = dict(topology_cache.get("trace_roles", {}) or {})
    primary_key = _render_primary_key(topology_cache)
    secondary_key = _render_secondary_key(topology_cache)
    dynamic_payload_dict = dict(dynamic_payload or {})
    trace_payload = _build_dynamic_trace_payload(
        topology_cache,
        dict(dynamic_payload_dict.get("bus", {}) or {}),
        dict(dynamic_payload_dict.get("line", {}) or {}),
    )
    line_traces = dict(trace_payload.get("line_traces", {}) or {})
    hover_trace = dict(trace_payload.get("line_hover_trace", {}) or {})
    bus_trace = dict(trace_payload.get("bus_trace", {}) or {})

    patch = Patch()
    for bucket_name in GRID_MAP_LINE_BUCKETS:
        trace_index = trace_roles.get(_bucket_trace_role(bucket_name))
        if trace_index is None:
            continue
        bucket_payload = dict(line_traces.get(bucket_name, {}) or {})
        patch["data"][int(trace_index)][primary_key] = list(bucket_payload.get(primary_key, []) or [])
        patch["data"][int(trace_index)][secondary_key] = list(bucket_payload.get(secondary_key, []) or [])

    hover_trace_index = trace_roles.get(GRID_MAP_TRACE_ROLE_LINE_HOVER)
    if hover_trace_index is not None:
        patch["data"][int(hover_trace_index)][primary_key] = list(hover_trace.get(primary_key, []) or [])
        patch["data"][int(hover_trace_index)][secondary_key] = list(hover_trace.get(secondary_key, []) or [])
        patch["data"][int(hover_trace_index)]["text"] = list(hover_trace.get("text", []) or [])

    bus_trace_index = trace_roles.get(GRID_MAP_TRACE_ROLE_BUS)
    if bus_trace_index is not None:
        patch["data"][int(bus_trace_index)]["marker"]["color"] = list(bus_trace.get("color", []) or [])
        patch["data"][int(bus_trace_index)]["text"] = list(bus_trace.get("text", []) or [])

    patch["layout"]["meta"]["grid_map_dynamic_revision"] = int(dynamic_revision or 0)
    return patch


def _render_primary_key(topology_cache: dict[str, Any]) -> str:
    return "lon" if _render_on_map(topology_cache) else "x"


def _render_secondary_key(topology_cache: dict[str, Any]) -> str:
    return "lat" if _render_on_map(topology_cache) else "y"


def _render_bus_points(topology_cache: dict[str, Any]) -> dict[str, list[float]]:
    if _render_on_map(topology_cache):
        return dict(topology_cache.get("geographic_bus_coords", {}) or {})
    return dict(topology_cache.get("bus_coords", {}) or {})


def _render_line_paths(topology_cache: dict[str, Any]) -> dict[str, list[list[float]]]:
    if _render_on_map(topology_cache):
        return dict(topology_cache.get("geographic_line_paths", {}) or {})
    return dict(topology_cache.get("line_paths", {}) or {})


def _render_trafo_paths(topology_cache: dict[str, Any]) -> dict[str, list[list[float]]]:
    if _render_on_map(topology_cache):
        return dict(topology_cache.get("geographic_trafo_paths", {}) or {})
    return dict(topology_cache.get("trafo_paths", {}) or {})


def _trace_roles() -> list[str]:
    return [
        GRID_MAP_TRACE_ROLE_LINE_NORMAL,
        GRID_MAP_TRACE_ROLE_LINE_WARNING,
        GRID_MAP_TRACE_ROLE_LINE_OVERLOADED,
        GRID_MAP_TRACE_ROLE_TRAFO,
        GRID_MAP_TRACE_ROLE_LINE_HOVER,
        GRID_MAP_TRACE_ROLE_BUS,
    ]


def _default_trace_index_meta() -> list[dict[str, Any]]:
    return [{"index": idx, "role": role} for idx, role in enumerate(_trace_roles())]


def _bucket_trace_role(bucket_name: str) -> str:
    return {
        "normal": GRID_MAP_TRACE_ROLE_LINE_NORMAL,
        "warning": GRID_MAP_TRACE_ROLE_LINE_WARNING,
        "overloaded": GRID_MAP_TRACE_ROLE_LINE_OVERLOADED,
    }[str(bucket_name)]


def _line_bucket_name(loading_pct: float | None) -> str:
    if loading_pct is None:
        return "normal"
    if float(loading_pct) > GRID_MAP_LINE_LOADING_LIMIT_PCT:
        return "overloaded"
    if float(loading_pct) >= GRID_MAP_LINE_WARNING_LIMIT_PCT:
        return "warning"
    return "normal"


def _line_bucket_style(bucket_name: str) -> dict[str, Any]:
    bucket = str(bucket_name)
    if bucket == "overloaded":
        return {"color": "#d93838", "width": 3.5}
    if bucket == "warning":
        return {"color": "#d28c00", "width": 2.5}
    return {"color": "#6d8f82", "width": 1.8}


def _bus_marker_size(bus_index: int, topology_cache: dict[str, Any]) -> float:
    battery_bus = ((topology_cache.get("metadata", {}) or {}).get("battery_bus"))
    if _render_on_map(topology_cache):
        return 14.0 if int(bus_index) == battery_bus else 10.0
    return 10.0 if int(bus_index) == battery_bus else 7.0


def _line_center_point(topology_cache: dict[str, Any], line_index: int) -> tuple[float, float]:
    centers = dict(topology_cache.get("line_center_points", {}) or {})
    point = centers.get(str(line_index)) or [0.0, 0.0]
    return float(point[0]), float(point[1])


def _empty_line_bucket_payload(topology_cache: dict[str, Any]) -> dict[str, Any]:
    return {
        _render_primary_key(topology_cache): [],
        _render_secondary_key(topology_cache): [],
    }


def _empty_line_hover_payload(topology_cache: dict[str, Any]) -> dict[str, Any]:
    return {
        _render_primary_key(topology_cache): [],
        _render_secondary_key(topology_cache): [],
        "text": [],
    }


def _append_line_path_to_bucket(bucket_payload: dict[str, Any], topology_cache: dict[str, Any], line_index: int) -> None:
    primary_key = _render_primary_key(topology_cache)
    secondary_key = _render_secondary_key(topology_cache)
    line_paths = _render_line_paths(topology_cache)
    path = list(line_paths.get(str(line_index)) or [])
    if len(path) < 2:
        return
    bucket_payload[primary_key].extend([float(point[0]) for point in path])
    bucket_payload[primary_key].append(None)
    bucket_payload[secondary_key].extend([float(point[1]) for point in path])
    bucket_payload[secondary_key].append(None)


def _build_dynamic_trace_payload(topology_cache: dict[str, Any], bus_dynamic: dict[str, Any], line_dynamic: dict[str, Any]) -> dict[str, Any]:
    bus_trace = {
        "color": [],
        "size": [],
        "text": [],
    }
    for bus_index in list(topology_cache.get("bus_order", []) or []):
        bus_state = dict(bus_dynamic.get(str(bus_index), {}) or {})
        vm_pu = _coerce_float(bus_state.get("vm_pu"))
        bus_trace["color"].append(_voltage_color(vm_pu))
        bus_trace["size"].append(_bus_marker_size(bus_index, topology_cache))
        bus_trace["text"].append(str(bus_state.get("hover") or f"Bus {bus_index}<br>Voltage=n/a"))

    line_traces = {bucket_name: _empty_line_bucket_payload(topology_cache) for bucket_name in GRID_MAP_LINE_BUCKETS}
    hover_trace = _empty_line_hover_payload(topology_cache)
    for line_index in list(topology_cache.get("line_hover_order", []) or []):
        line_state = dict(line_dynamic.get(str(line_index), {}) or {})
        loading_pct = _coerce_float(line_state.get("loading_pct"))
        bucket_name = _line_bucket_name(loading_pct)
        _append_line_path_to_bucket(line_traces[bucket_name], topology_cache, line_index)
        center_primary, center_secondary = _line_center_point(topology_cache, line_index)
        hover_trace[_render_primary_key(topology_cache)].append(center_primary)
        hover_trace[_render_secondary_key(topology_cache)].append(center_secondary)
        hover_trace["text"].append(str(line_state.get("hover") or f"Line {line_index}<br>Loading=n/a"))

    return {
        "bus_trace": bus_trace,
        "line_traces": line_traces,
        "line_hover_trace": hover_trace,
    }


def _build_static_trace_dicts(topology_cache: dict[str, Any], dynamic_payload: dict[str, Any]) -> list[dict[str, Any]]:
    primary_key = _render_primary_key(topology_cache)
    secondary_key = _render_secondary_key(topology_cache)
    trace_roles = dict(topology_cache.get("trace_roles", {}) or {})
    bus_points = _render_bus_points(topology_cache)
    bus_primary = []
    bus_secondary = []
    for bus_index in list(topology_cache.get("bus_order", []) or []):
        point = list(bus_points.get(str(bus_index)) or [])
        if len(point) < 2:
            continue
        bus_primary.append(float(point[0]))
        bus_secondary.append(float(point[1]))

    trafo_primary = []
    trafo_secondary = []
    for trafo_index in list(topology_cache.get("trafo_order", []) or []):
        path = list(_render_trafo_paths(topology_cache).get(str(trafo_index)) or [])
        if len(path) < 2:
            continue
        trafo_primary.extend([float(point[0]) for point in path])
        trafo_primary.append(None)
        trafo_secondary.extend([float(point[1]) for point in path])
        trafo_secondary.append(None)
    if trafo_primary and trafo_primary[-1] is None:
        trafo_primary.pop()
    if trafo_secondary and trafo_secondary[-1] is None:
        trafo_secondary.pop()

    line_traces = dict(dynamic_payload.get("line_traces", {}) or {})
    bus_trace = dict(dynamic_payload.get("bus_trace", {}) or {})
    hover_trace = dict(dynamic_payload.get("line_hover_trace", {}) or {})

    line_trace_type = "scattermap" if _render_on_map(topology_cache) else "scatter"
    bus_marker = dict(size=list(bus_trace.get("size", []) or []), color=list(bus_trace.get("color", []) or []))
    if line_trace_type == "scatter":
        bus_marker["line"] = dict(color="#ffffff", width=1)
    else:
        bus_marker["opacity"] = 0.95

    traces_by_role = {
        GRID_MAP_TRACE_ROLE_LINE_NORMAL: {
            "type": line_trace_type,
            "mode": "lines",
            primary_key: list((line_traces.get("normal", {}) or {}).get(primary_key, []) or []),
            secondary_key: list((line_traces.get("normal", {}) or {}).get(secondary_key, []) or []),
            "hoverinfo": "skip",
            "name": "Lines",
            "showlegend": False,
            "line": dict(_line_bucket_style("normal")),
        },
        GRID_MAP_TRACE_ROLE_LINE_WARNING: {
            "type": line_trace_type,
            "mode": "lines",
            primary_key: list((line_traces.get("warning", {}) or {}).get(primary_key, []) or []),
            secondary_key: list((line_traces.get("warning", {}) or {}).get(secondary_key, []) or []),
            "hoverinfo": "skip",
            "name": "Lines",
            "showlegend": False,
            "line": dict(_line_bucket_style("warning")),
        },
        GRID_MAP_TRACE_ROLE_LINE_OVERLOADED: {
            "type": line_trace_type,
            "mode": "lines",
            primary_key: list((line_traces.get("overloaded", {}) or {}).get(primary_key, []) or []),
            secondary_key: list((line_traces.get("overloaded", {}) or {}).get(secondary_key, []) or []),
            "hoverinfo": "skip",
            "name": "Lines",
            "showlegend": False,
            "line": dict(_line_bucket_style("overloaded")),
        },
        GRID_MAP_TRACE_ROLE_TRAFO: {
            "type": line_trace_type,
            "mode": "lines",
            primary_key: trafo_primary,
            secondary_key: trafo_secondary,
            "hoverinfo": "skip",
            "name": "Transformers",
            "showlegend": False,
            "line": dict(color="#2f5b4e", width=3),
        },
        GRID_MAP_TRACE_ROLE_LINE_HOVER: {
            "type": line_trace_type,
            "mode": "markers",
            primary_key: list(hover_trace.get(primary_key, []) or []),
            secondary_key: list(hover_trace.get(secondary_key, []) or []),
            "hoverinfo": "text",
            "text": list(hover_trace.get("text", []) or []),
            "hovertemplate": "%{text}<extra></extra>",
            "name": "Line Hover",
            "showlegend": False,
            "marker": dict(
                size=GRID_MAP_LINE_HOVER_MARKER_SIZE,
                color=GRID_MAP_LINE_HOVER_MARKER_COLOR,
            ),
        },
        GRID_MAP_TRACE_ROLE_BUS: {
            "type": line_trace_type,
            "mode": "markers",
            primary_key: bus_primary,
            secondary_key: bus_secondary,
            "hoverinfo": "text",
            "text": list(bus_trace.get("text", []) or []),
            "hovertemplate": "%{text}<extra></extra>",
            "name": "Buses",
            "showlegend": False,
            "marker": bus_marker,
        },
    }
    return [traces_by_role[role] for role in _trace_roles()]


def _build_low_trace_figure_dict(
    topology_cache: dict[str, Any],
    dynamic_payload: dict[str, Any],
    *,
    title: str,
    uirevision_key: str,
    topology_revision: Any,
    dynamic_revision: int,
) -> dict[str, Any]:
    plot_theme = dict(DEFAULT_PLOT_THEME)
    data = _build_static_trace_dicts(topology_cache, dynamic_payload)
    layout_meta = {
        "grid_map_dynamic_revision": int(dynamic_revision or 0),
        **_grid_map_fit_meta(topology_cache, topology_revision),
    }
    layout = {
        "height": 720,
        "margin": {"l": 20, "r": 20, "t": 20, "b": 20},
        "paper_bgcolor": plot_theme["paper_bg"],
        "font": {"color": plot_theme["text"], "family": plot_theme["font_family"], "size": 12},
        "uirevision": uirevision_key,
        "meta": layout_meta,
    }
    if _render_on_map(topology_cache):
        center = dict(topology_cache.get("geographic_center", {}) or {})
        layout["map"] = {
            "style": _map_style_for_background_mode(topology_cache.get("map_background_mode")),
            "center": {
                "lon": _coerce_float(center.get("lon")) or 0.0,
                "lat": _coerce_float(center.get("lat")) or 0.0,
            },
            "zoom": _map_zoom_for_bounds(
                dict(topology_cache.get("geographic_bounds", {}) or {}),
                viewport_width_px=GRID_MAP_FALLBACK_VIEWPORT_WIDTH_PX,
                viewport_height_px=GRID_MAP_FALLBACK_VIEWPORT_HEIGHT_PX,
                padding_px=GRID_MAP_STARTUP_FIT_PADDING_PX,
            ),
        }
    else:
        bounds = dict(topology_cache.get("bounds", {}) or {})
        x_min = _coerce_float(bounds.get("x_min"))
        x_max = _coerce_float(bounds.get("x_max"))
        y_min = _coerce_float(bounds.get("y_min"))
        y_max = _coerce_float(bounds.get("y_max"))
        x_span = 1.0 if x_min is None or x_max is None else max(1e-9, x_max - x_min)
        y_span = 1.0 if y_min is None or y_max is None else max(1e-9, y_max - y_min)
        x_pad = x_span * 0.05
        y_pad = y_span * 0.05
        layout["plot_bgcolor"] = "#f8fcfa"
        layout["xaxis"] = {
            "visible": False,
            "showgrid": False,
            "zeroline": False,
            "range": None if x_min is None or x_max is None else [x_min - x_pad, x_max + x_pad],
        }
        layout["yaxis"] = {
            "visible": False,
            "showgrid": False,
            "zeroline": False,
            "scaleanchor": "x",
            "scaleratio": 1,
            "range": None if y_min is None or y_max is None else [y_min - y_pad, y_max + y_pad],
        }
    return {"data": data, "layout": layout}


def _build_empty_grid_map_figure_dict(*, title: str, uirevision_key: str, topology_revision: Any, dynamic_revision: int) -> dict[str, Any]:
    plot_theme = dict(DEFAULT_PLOT_THEME)
    return {
        "data": [],
        "layout": {
            "height": 720,
            "paper_bgcolor": plot_theme["paper_bg"],
            "plot_bgcolor": plot_theme["paper_bg"],
            "font": {"color": plot_theme["text"], "family": plot_theme["font_family"]},
            "margin": {"l": 20, "r": 20, "t": 20, "b": 20},
            "uirevision": uirevision_key,
            "meta": {
                "grid_map_topology_revision": topology_revision,
                "grid_map_dynamic_revision": int(dynamic_revision or 0),
                "grid_map_coordinate_mode": "schematic",
            },
            "annotations": [
                {
                    "text": "Grid topology unavailable.",
                    "x": 0.5,
                    "y": 0.5,
                    "xref": "paper",
                    "yref": "paper",
                    "showarrow": False,
                }
            ],
            "xaxis": {"visible": False},
            "yaxis": {"visible": False},
        },
    }


def _build_pandapower_traces(
    topology_cache: dict[str, Any],
    dynamic_payload: dict[str, Any] | None,
) -> list[Any]:
    try:
        from pandapower.plotting.plotly import create_bus_trace, create_line_trace, create_trafo_trace, draw_traces
    except Exception as exc:
        raise RuntimeError("pandapower plotly helpers unavailable") from exc

    source_plot_net = topology_cache.get("plot_net")
    if source_plot_net is None:
        raise RuntimeError("prepared plot net is unavailable")
    plot_net = _prepare_plot_net_for_pandapower_traces(source_plot_net)

    dynamic_payload = dict(dynamic_payload or {})
    bus_dynamic = dict(dynamic_payload.get("bus", {}) or {})
    line_dynamic = dict(dynamic_payload.get("line", {}) or {})

    line_order = [int(index) for index in list(topology_cache.get("line_order", []) or [])]
    bus_order = [int(index) for index in list(topology_cache.get("bus_order", []) or [])]
    trafo_order = [int(index) for index in list(topology_cache.get("trafo_order", []) or [])]

    line_info = pd.Series(
        data=[str(dict(line_dynamic.get(str(index), {}) or {}).get("hover") or f"Line {index}") for index in line_order],
        index=line_order,
        dtype=object,
    )
    line_vals = [_coerce_float(dict(line_dynamic.get(str(index), {}) or {}).get("loading_pct")) or 0.0 for index in line_order]

    bus_info = pd.Series(
        data=[str(dict(bus_dynamic.get(str(index), {}) or {}).get("hover") or f"Bus {index}") for index in bus_order],
        index=bus_order,
        dtype=object,
    )
    bus_voltage_values = [_coerce_float(dict(bus_dynamic.get(str(index), {}) or {}).get("vm_pu")) for index in bus_order]
    bus_colors = [_voltage_color(value) for value in bus_voltage_values]

    traces: list[Any] = []
    if line_order:
        line_trace = create_line_trace(
            plot_net,
            lines=line_order,
            use_line_geo=True,
            infofunc=line_info,
            trace_name="Lines",
            cmap=True,
            show_colorbar=False,
            cmap_vals=line_vals,
            cmin=0.0,
            cmax=100.0,
        )
        if isinstance(line_trace, list):
            traces.extend(line_trace)
        elif line_trace is not None:
            traces.append(line_trace)

    if trafo_order:
        trafo_info = pd.Series(
            data=[f"Transformer {index}" for index in trafo_order],
            index=trafo_order,
            dtype=object,
        )
        trafo_trace = create_trafo_trace(
            plot_net,
            trafos=trafo_order,
            infofunc=trafo_info,
            use_line_geo=False,
            trace_name="Transformers",
        )
        if isinstance(trafo_trace, list):
            traces.extend(trafo_trace)
        elif trafo_trace is not None:
            traces.append(trafo_trace)

    if bus_order:
        bus_trace = create_bus_trace(
            plot_net,
            buses=bus_order,
            infofunc=bus_info,
            trace_name="Buses",
            cmap=False,
            size=8,
        )
        _apply_marker_colors(bus_trace, bus_colors)
        if isinstance(bus_trace, list):
            traces.extend(bus_trace)
        elif bus_trace is not None:
            traces.append(bus_trace)

    return traces


def _build_pandapower_figure_dict(
    topology_cache: dict[str, Any],
    dynamic_payload: dict[str, Any] | None,
    *,
    title: str,
    uirevision_key: str,
    topology_revision: Any,
    dynamic_revision: int,
) -> dict[str, Any]:
    try:
        from pandapower.plotting.plotly import draw_traces
    except Exception as exc:
        logging.warning("Grid map: pandapower plotly figure builder unavailable: %s", exc)
        return _build_empty_grid_map_figure_dict(
            title=title,
            uirevision_key=uirevision_key,
            topology_revision=topology_revision,
            dynamic_revision=dynamic_revision,
        )

    try:
        traces = _build_pandapower_traces(topology_cache, dynamic_payload)
        if not traces:
            return _build_empty_grid_map_figure_dict(
                title=title,
                uirevision_key=uirevision_key,
                topology_revision=topology_revision,
                dynamic_revision=dynamic_revision,
            )

        draw_kwargs = {
            "showlegend": False,
            "filename": None,
            "auto_open": False,
            "on_map": _render_on_map(topology_cache),
        }
        if _render_on_map(topology_cache):
            draw_kwargs["map_style"] = _map_style_for_background_mode(topology_cache.get("map_background_mode"))
            draw_kwargs["zoomlevel"] = _map_zoom_for_bounds(
                dict(topology_cache.get("geographic_bounds", {}) or {}),
                viewport_width_px=GRID_MAP_FALLBACK_VIEWPORT_WIDTH_PX,
                viewport_height_px=GRID_MAP_FALLBACK_VIEWPORT_HEIGHT_PX,
                padding_px=GRID_MAP_STARTUP_FIT_PADDING_PX,
            )
        map_loggers = [
            logging.getLogger("pandapower.plotting.plotly.mapbox_plot"),
            logging.getLogger("pandapower.plotting.plotly.traces"),
            logging.getLogger("pandapower.plotting.plotly.draw_layers"),
        ]
        previous_map_logger_levels = [logger.level for logger in map_loggers]
        if _render_on_map(topology_cache):
            for logger, previous_level in zip(map_loggers, previous_map_logger_levels):
                logger.setLevel(max(logging.ERROR, previous_level))
        try:
            fig = draw_traces(traces, **draw_kwargs)
        finally:
            if _render_on_map(topology_cache):
                for logger, previous_level in zip(map_loggers, previous_map_logger_levels):
                    logger.setLevel(previous_level)
        fig_dict = fig.to_dict() if hasattr(fig, "to_dict") else go.Figure(fig).to_dict()
    except Exception as exc:
        logging.warning("Grid map: failed to build pandapower figure data: %s", exc)
        return _build_empty_grid_map_figure_dict(
            title=title,
            uirevision_key=uirevision_key,
            topology_revision=topology_revision,
            dynamic_revision=dynamic_revision,
        )

    plot_theme = dict(DEFAULT_PLOT_THEME)
    layout = dict(fig_dict.get("layout", {}) or {})
    layout.pop("title", None)
    layout["height"] = 720
    layout["margin"] = dict(l=20, r=20, t=20, b=20)
    layout["paper_bgcolor"] = plot_theme["paper_bg"]
    layout["font"] = dict(color=plot_theme["text"], family=plot_theme["font_family"], size=12)
    layout["uirevision"] = uirevision_key
    layout["meta"] = {
        "grid_map_dynamic_revision": int(dynamic_revision or 0),
        **_grid_map_fit_meta(topology_cache, topology_revision),
    }
    if _render_on_map(topology_cache):
        map_layout = dict(layout.get("map", {}) or {})
        center = dict(topology_cache.get("geographic_center", {}) or {})
        map_layout["style"] = _map_style_for_background_mode(topology_cache.get("map_background_mode"))
        map_layout["center"] = {
            "lon": _coerce_float(center.get("lon")) or 0.0,
            "lat": _coerce_float(center.get("lat")) or 0.0,
        }
        map_layout["zoom"] = _map_zoom_for_bounds(
            dict(topology_cache.get("geographic_bounds", {}) or {}),
            viewport_width_px=GRID_MAP_FALLBACK_VIEWPORT_WIDTH_PX,
            viewport_height_px=GRID_MAP_FALLBACK_VIEWPORT_HEIGHT_PX,
            padding_px=GRID_MAP_STARTUP_FIT_PADDING_PX,
        )
        layout["map"] = map_layout
    fig_dict["layout"] = layout
    return fig_dict


def build_topology_cache(config: dict[str, Any] | None = None) -> dict[str, Any]:
    simulator_module = _import_simulator_module()
    requested_background_mode = _requested_background_mode_from_config(config)
    assets = None
    if hasattr(simulator_module, "get_base_network_copy"):
        net = simulator_module.get_base_network_copy()
    else:
        assets = simulator_module._load_assets()
        net = copy.deepcopy(assets["base_net"])
    if hasattr(simulator_module, "get_metadata"):
        metadata = dict(simulator_module.get_metadata() or {})
    else:
        if assets is None:
            assets = simulator_module._load_assets()
        metadata = dict((assets.get("metadata") if isinstance(assets, dict) else {}) or {})

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
    map_background_mode = GRID_MAP_BACKGROUND_MODE_NONE
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
                map_background_mode = requested_background_mode
                map_background_enabled = map_background_mode != GRID_MAP_BACKGROUND_MODE_NONE
                map_background_reason = None
            else:
                map_background_reason = "coordinate_conversion_empty"
        except Exception as exc:
            logging.warning("Grid map: pandapower CRS conversion failed: %s", exc)
            map_background_reason = f"coordinate_conversion_failed:{exc}"

    render_geographic = coordinate_mode == "geographic"
    plot_bus_coords = geographic_bus_coords if render_geographic and geographic_bus_coords else bus_coords
    plot_line_paths = geographic_line_paths if render_geographic and geographic_line_paths else line_paths
    plot_net = _prepare_plot_net(net, bus_coords=plot_bus_coords, line_paths=plot_line_paths)
    topology_revision = int(pd.Timestamp.utcnow().value)
    render_line_paths = plot_line_paths
    line_center_points = {
        str(index): [float(point[0]), float(point[1])]
        for index, path in render_line_paths.items()
        for point in [_line_center(path)]
    }
    placeholder_cache = {
        "plot_net": plot_net,
        "metadata": metadata,
        "bus_order": [int(index) for index in sorted(bus_coords.keys())],
        "line_order": [int(index) for index in sorted(line_paths.keys())],
        "trafo_order": [int(index) for index in sorted(trafo_paths.keys())],
        "coordinate_mode": coordinate_mode,
        "source_crs": source_crs,
        "target_crs": target_crs,
        "map_background_mode": map_background_mode,
        "map_background_enabled": map_background_enabled,
        "map_background_reason": map_background_reason,
        "bounds": dict(bounds or {}),
        "bus_coords": {str(index): [float(point[0]), float(point[1])] for index, point in bus_coords.items()},
        "line_paths": {
            str(index): [[float(point[0]), float(point[1])] for point in path]
            for index, path in line_paths.items()
        },
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
        "line_center_points": line_center_points,
        "line_hover_order": [int(index) for index in sorted(line_paths.keys())],
        "trace_roles": {role: idx for idx, role in enumerate(_trace_roles())},
        "figure_renderer": "low-trace",
    }
    initial_figure = _build_low_trace_figure_dict(
        placeholder_cache,
        _build_dynamic_trace_payload(placeholder_cache, {}, {}),
        title="Distribution Grid Map",
        uirevision_key="grid-map",
        topology_revision=topology_revision,
        dynamic_revision=0,
    )
    trace_index_meta = _default_trace_index_meta()

    return {
        "plot_net": plot_net,
        "initial_figure": initial_figure,
        "trace_index_meta": trace_index_meta,
        "topology_revision": topology_revision,
        "figure_renderer": "low-trace",
        "metadata": metadata,
        "coordinate_mode": coordinate_mode,
        "source_crs": source_crs,
        "target_crs": target_crs,
        "map_background_mode": map_background_mode,
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
        "line_center_points": dict(line_center_points),
        "line_hover_order": [int(index) for index in sorted(line_paths.keys())],
        "trace_roles": {role: idx for idx, role in enumerate(_trace_roles())},
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
        "trace_count": len((topology_cache.get("initial_figure", {}) or {}).get("data", []) if isinstance(topology_cache.get("initial_figure"), dict) else []),
        "coordinate_mode": str(topology_cache.get("coordinate_mode") or "schematic"),
        "source_crs": topology_cache.get("source_crs"),
        "target_crs": topology_cache.get("target_crs"),
        "map_background_mode": str(topology_cache.get("map_background_mode") or GRID_MAP_BACKGROUND_MODE_NONE),
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
    battery_voltage_pu = _coerce_float(result.get("battery_bus_vm_pu"))
    finite_vm_series = vm_series[pd.notna(vm_series)] if not vm_series.empty else pd.Series(dtype=float)

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
        if finite_vm_series.empty:
            voltage_violations = 0
        else:
            voltage_violations = int(
                ((finite_vm_series < GRID_MAP_VOLTAGE_MIN_PU) | (finite_vm_series > GRID_MAP_VOLTAGE_MAX_PU)).sum()
            )

    overloaded_lines = result.get("num_overloaded_lines")
    if overloaded_lines is None:
        overloaded_lines = int((line_loading > GRID_MAP_LINE_LOADING_LIMIT_PCT).sum()) if not line_loading.empty else 0

    voltage_bucket_counts = {}
    for bucket_name, lower_bound, upper_bound in GRID_MAP_VOLTAGE_BUCKET_SPECS:
        if finite_vm_series.empty:
            voltage_bucket_counts[bucket_name] = 0
            continue
        mask = pd.Series(True, index=finite_vm_series.index)
        if lower_bound is not None:
            mask &= finite_vm_series >= float(lower_bound)
        if upper_bound is not None:
            mask &= finite_vm_series < float(upper_bound)
        voltage_bucket_counts[bucket_name] = int(mask.sum())

    return {
        "battery_voltage_pu": battery_voltage_pu,
        "min_voltage_pu": min_voltage,
        "max_voltage_pu": max_voltage,
        "num_voltage_violations": int(voltage_violations or 0),
        "max_line_loading_pct": max_line_loading,
        "num_overloaded_lines": int(overloaded_lines or 0),
        **voltage_bucket_counts,
    }


def build_dynamic_payload(power_flow_result: dict[str, Any], topology_cache: dict[str, Any]) -> dict[str, Any]:
    results_tables = dict(power_flow_result.get("results_tables", {}) or {})
    res_bus = results_tables.get("res_bus")
    res_line = results_tables.get("res_line")
    res_trafo = results_tables.get("res_trafo")

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
    trafo_loading = (
        pd.to_numeric(res_trafo["loading_percent"], errors="coerce")
        if isinstance(res_trafo, pd.DataFrame) and "loading_percent" in res_trafo.columns
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

    trafo_dynamic = {}
    for trafo_index in topology_cache.get("trafo_order", []):
        loading_pct = _coerce_float(trafo_loading.get(trafo_index))
        status = "unknown"
        if loading_pct is not None:
            status = "overloaded" if loading_pct > GRID_MAP_LINE_LOADING_LIMIT_PCT else "ok"
        trafo_dynamic[str(trafo_index)] = {
            "loading_pct": loading_pct,
            "status": status,
            "hover": (
                f"Transformer {trafo_index}<br>Loading={loading_pct:.1f}%"
                if loading_pct is not None
                else f"Transformer {trafo_index}<br>Loading=n/a"
            ),
        }

    return {
        "bus": bus_dynamic,
        "line": line_dynamic,
        "trafo": trafo_dynamic,
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
    q_mvar = -float(q_kvar) / 1000.0
    timestamp_iso = serialize_iso_with_tz(timestamp, tz=tz)

    result = simulator_module.run_power_flow(
        battery_p_mw=p_mw,
        battery_q_mvar=q_mvar,
        timestamp_iso=timestamp_iso,
    )
    result = _normalize_power_flow_result(simulator_module, result)
    return {
        "power_flow_result": result,
        "requested_timestamp_local": timestamp_iso,
        "battery_input_p_kw": float(p_kw),
        "battery_input_q_kvar": float(q_kvar),
        "battery_input_p_mw": float(p_mw),
        "battery_input_q_mvar": float(q_mvar),
    }


def write_grid_map_optional_voltage_point(
    config: dict[str, Any],
    shared_data: dict[str, Any],
    run_payload: dict[str, Any],
) -> dict[str, Any]:
    power_flow_result = dict(run_payload.get("power_flow_result", {}) or {})
    voltage_kv = _coerce_float(power_flow_result.get("battery_bus_vm_kv"))
    if voltage_kv is None:
        return {
            "state": "skipped",
            "message": "voltage_unavailable",
            "transport_mode": None,
            "value_kv": None,
            "targets": [],
        }

    with shared_data["lock"]:
        transport_mode = str(shared_data.get("transport_mode", "local") or "local")

    endpoint = resolve_grid_map_voltage_write_endpoint(config, transport_mode)
    if endpoint is None:
        return {
            "state": "skipped",
            "message": "point_not_configured",
            "transport_mode": transport_mode,
            "value_kv": voltage_kv,
            "targets": [],
        }

    points = dict(endpoint.get("points", {}) or {})
    point_spec = points.get("v_poi_write")
    if point_spec is None:
        return {
            "state": "skipped",
            "message": "point_not_configured",
            "transport_mode": transport_mode,
            "value_kv": voltage_kv,
            "targets": [],
        }

    target_result = {
        "host": endpoint.get("host"),
        "port": endpoint.get("port"),
        "state": "skipped",
        "message": None,
        "value_kv": voltage_kv,
    }
    targets = [target_result]

    access = str(point_spec.get("access", "")).strip().lower()
    if "w" not in access:
        logging.warning(
            "Grid map: optional voltage write skipped (transport=%s host=%s port=%s reason=point_not_write_capable access=%s).",
            transport_mode,
            endpoint.get("host"),
            endpoint.get("port"),
            access or "unknown",
        )
        target_result["message"] = "point_not_write_capable"
        return {
            "state": "skipped",
            "message": "point_not_write_capable",
            "transport_mode": transport_mode,
            "value_kv": voltage_kv,
            "targets": targets,
        }

    client = ModbusClient(host=endpoint["host"], port=endpoint["port"])
    try:
        if not client.open():
            logging.warning(
                "Grid map: optional voltage write failed (transport=%s host=%s port=%s reason=connect_failed).",
                transport_mode,
                endpoint.get("host"),
                endpoint.get("port"),
            )
            target_result["state"] = "failed"
            target_result["message"] = "connect_failed"
            return {
                "state": "failed",
                "message": "write_failed",
                "transport_mode": transport_mode,
                "value_kv": voltage_kv,
                "targets": targets,
            }

        ok = bool(write_point_internal(client, endpoint, "v_poi_write", voltage_kv))
        if ok:
            logging.debug(
                "Grid map: optional voltage write ok (transport=%s host=%s port=%s value_kv=%.6f).",
                transport_mode,
                endpoint.get("host"),
                endpoint.get("port"),
                voltage_kv,
            )
            target_result["state"] = "ok"
            return {
                "state": "ok",
                "message": None,
                "transport_mode": transport_mode,
                "value_kv": voltage_kv,
                "targets": targets,
            }

        logging.warning(
            "Grid map: optional voltage write failed (transport=%s host=%s port=%s reason=write_failed value_kv=%.6f).",
            transport_mode,
            endpoint.get("host"),
            endpoint.get("port"),
            voltage_kv,
        )
        target_result["state"] = "failed"
        target_result["message"] = "write_failed"
        return {
            "state": "failed",
            "message": "write_failed",
            "transport_mode": transport_mode,
            "value_kv": voltage_kv,
            "targets": targets,
        }
    except Exception as exc:
        logging.warning(
            "Grid map: optional voltage write failed (transport=%s host=%s port=%s error=%s).",
            transport_mode,
            endpoint.get("host"),
            endpoint.get("port"),
            exc,
        )
        target_result["state"] = "failed"
        target_result["message"] = str(exc)
        return {
            "state": "failed",
            "message": "write_failed",
            "transport_mode": transport_mode,
            "value_kv": voltage_kv,
            "targets": targets,
        }
    finally:
        try:
            client.close()
        except Exception:
            pass

    return {
        "state": "skipped",
        "message": "point_not_configured",
        "transport_mode": transport_mode,
        "value_kv": voltage_kv,
        "targets": targets,
    }


def publish_grid_map_topology(shared_data: dict[str, Any], *, topology_cache: dict[str, Any]) -> None:
    topology_meta = summarize_topology_cache(topology_cache)
    with shared_data["lock"]:
        runtime_state = shared_data.setdefault(GRID_MAP_STATUS_KEY, default_grid_map_runtime(5.0))
        runtime_state["topology_ready"] = True
        runtime_state["topology_error"] = None
        runtime_state["topology_cache"] = topology_cache
        runtime_state["topology_cache_meta"] = topology_meta
        runtime_state["initial_figure"] = topology_cache.get("initial_figure")
        runtime_state["trace_index_meta"] = topology_cache.get("trace_index_meta")
        runtime_state["topology_revision"] = topology_cache.get("topology_revision")
        runtime_state["dynamic_revision"] = 0
        runtime_state["coordinate_mode"] = str((topology_meta or {}).get("coordinate_mode") or "schematic")
        runtime_state["source_crs"] = (topology_meta or {}).get("source_crs")
        runtime_state["target_crs"] = (topology_meta or {}).get("target_crs")
        runtime_state["map_background_mode"] = str(
            (topology_meta or {}).get("map_background_mode") or GRID_MAP_BACKGROUND_MODE_NONE
        )
        runtime_state["map_background_enabled"] = bool((topology_meta or {}).get("map_background_enabled", False))
        runtime_state["map_background_reason"] = (topology_meta or {}).get("map_background_reason")


def publish_grid_map_topology_error(shared_data: dict[str, Any], *, error_text: str) -> None:
    with shared_data["lock"]:
        runtime_state = shared_data.setdefault(GRID_MAP_STATUS_KEY, default_grid_map_runtime(5.0))
        runtime_state["state"] = "error"
        runtime_state["topology_ready"] = False
        runtime_state["topology_error"] = str(error_text)
        runtime_state["last_error"] = str(error_text)
        runtime_state["initial_figure"] = None
        runtime_state["trace_index_meta"] = None
        runtime_state["topology_revision"] = None
        runtime_state["dynamic_revision"] = 0
        runtime_state["coordinate_mode"] = "schematic"
        runtime_state["source_crs"] = None
        runtime_state["target_crs"] = None
        runtime_state["map_background_mode"] = GRID_MAP_BACKGROUND_MODE_NONE
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
    scenario_results: dict[str, Any] | None = None,
) -> None:
    pf_result = dict(run_payload.get("power_flow_result", {}) or {})
    selected_local = pf_result.get("selected_timestamp_local")
    selected_utc = pf_result.get("selected_timestamp_utc")
    requested_local = run_payload.get("requested_timestamp_local")
    with shared_data["lock"]:
        runtime_state = shared_data.setdefault(GRID_MAP_STATUS_KEY, default_grid_map_runtime(5.0))
        normalized_scenarios = _normalize_grid_map_scenario_results(runtime_state.get("scenario_results"))
        provided_scenarios = _normalize_grid_map_scenario_results(scenario_results)
        for scenario_key in normalized_scenarios:
            normalized_scenarios[scenario_key].update(provided_scenarios.get(scenario_key, {}))
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
        runtime_state["scenario_results"] = normalized_scenarios
        runtime_state["dynamic_revision"] = int(runtime_state.get("dynamic_revision", 0) or 0) + 1
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
            runtime_state["battery_input_q_mvar"] = None if q_kvar is None else -float(q_kvar) / 1000.0
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
        current["initial_figure"] = current.get("initial_figure")
        dynamic_payload = current.get("dynamic_payload")
        current["dynamic_payload"] = copy.deepcopy(dynamic_payload) if isinstance(dynamic_payload, dict) else dynamic_payload
        summary = current.get("summary")
        current["summary"] = dict(summary or {}) if isinstance(summary, dict) else summary
        scenario_results = current.get("scenario_results")
        current["scenario_results"] = _normalize_grid_map_scenario_results(copy.deepcopy(scenario_results))
        meta = current.get("topology_cache_meta")
        current["topology_cache_meta"] = dict(meta or {}) if isinstance(meta, dict) else meta
        trace_meta = current.get("trace_index_meta")
        current["trace_index_meta"] = copy.deepcopy(trace_meta) if isinstance(trace_meta, list) else trace_meta
        return current


def _voltage_color(vm_pu: float | None) -> str:
    if vm_pu is None:
        return GRID_MAP_VOLTAGE_COLOR_MISSING
    if vm_pu < 0.925:
        return GRID_MAP_VOLTAGE_COLOR_RED
    if vm_pu < 0.95:
        return GRID_MAP_VOLTAGE_COLOR_AMBER
    if vm_pu < 0.975:
        return GRID_MAP_VOLTAGE_COLOR_YELLOW_GREEN
    if vm_pu < 1.025:
        return GRID_MAP_VOLTAGE_COLOR_GREEN
    if vm_pu < 1.05:
        return GRID_MAP_VOLTAGE_COLOR_DARK_CYAN_GREEN
    if vm_pu < 1.075:
        return GRID_MAP_VOLTAGE_COLOR_LIGHT_BLUE_GREEN
    return GRID_MAP_VOLTAGE_COLOR_BLUE


def _apply_marker_colors(trace_or_traces: Any, colors: list[str]) -> None:
    for trace in list(trace_or_traces) if isinstance(trace_or_traces, list) else [trace_or_traces]:
        if isinstance(trace, dict):
            marker = dict(trace.get("marker", {}) or {})
            marker["color"] = list(colors)
            marker.pop("colorscale", None)
            marker.pop("colorbar", None)
            marker["showscale"] = False
            trace["marker"] = marker
            continue
        marker = getattr(trace, "marker", None)
        if marker is None:
            continue
        try:
            marker.color = list(colors)
            if hasattr(marker, "showscale"):
                marker.showscale = False
            if hasattr(marker, "colorscale"):
                marker.colorscale = None
        except Exception:
            continue


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


def _map_zoom_for_bounds(
    bounds: dict[str, float] | None,
    *,
    viewport_width_px: Any = GRID_MAP_FALLBACK_VIEWPORT_WIDTH_PX,
    viewport_height_px: Any = GRID_MAP_FALLBACK_VIEWPORT_HEIGHT_PX,
    padding_px: Any = GRID_MAP_STARTUP_FIT_PADDING_PX,
) -> float:
    if not isinstance(bounds, dict):
        return 13.0
    lon_min = _coerce_float(bounds.get("x_min"))
    lon_max = _coerce_float(bounds.get("x_max"))
    lat_min = _coerce_float(bounds.get("y_min"))
    lat_max = _coerce_float(bounds.get("y_max"))
    if None in (lon_min, lon_max, lat_min, lat_max):
        return 13.0
    view_width = _coerce_float(viewport_width_px) or float(GRID_MAP_FALLBACK_VIEWPORT_WIDTH_PX)
    view_height = _coerce_float(viewport_height_px) or float(GRID_MAP_FALLBACK_VIEWPORT_HEIGHT_PX)
    padding = max(0.0, _coerce_float(padding_px) or float(GRID_MAP_STARTUP_FIT_PADDING_PX))
    available_width = max(1.0, view_width - (2.0 * padding))
    available_height = max(1.0, view_height - (2.0 * padding))

    lon_span = abs(lon_max - lon_min)
    lon_fraction = lon_span / 360.0
    y_min = _mercator_world_y_fraction(lat_min)
    y_max = _mercator_world_y_fraction(lat_max)
    if y_min is None or y_max is None:
        return 13.0
    lat_fraction = abs(y_max - y_min)

    if lon_fraction < 1e-12 and lat_fraction < 1e-12:
        return float(GRID_MAP_DEGENERATE_BOUNDS_ZOOM)

    tile_size = 512.0
    zoom_x = float("inf") if lon_fraction < 1e-12 else math.log2(available_width / (tile_size * lon_fraction))
    zoom_y = float("inf") if lat_fraction < 1e-12 else math.log2(available_height / (tile_size * lat_fraction))
    zoom = min(zoom_x, zoom_y)
    if not math.isfinite(zoom):
        zoom = float(GRID_MAP_DEGENERATE_BOUNDS_ZOOM)
    return float(min(GRID_MAP_MAX_ZOOM, max(GRID_MAP_MIN_ZOOM, zoom)))


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
            paper_bgcolor=plot_theme["paper_bg"],
            plot_bgcolor=plot_theme["paper_bg"],
            font=dict(color=plot_theme["text"], family=plot_theme["font_family"]),
            margin=dict(l=20, r=20, t=20, b=20),
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
        height=720,
        margin=dict(l=20, r=20, t=20, b=20),
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
        height=720,
        margin=dict(l=20, r=20, t=20, b=20),
        paper_bgcolor=plot_theme["paper_bg"],
        font=dict(color=plot_theme["text"], family=plot_theme["font_family"], size=12),
        uirevision=uirevision_key,
        meta={
            "grid_map_dynamic_revision": int(bool(dynamic_payload)),
            **_grid_map_fit_meta(topology_cache, topology_cache.get("topology_revision")),
        },
        map=dict(
            style=_map_style_for_background_mode(topology_cache.get("map_background_mode")),
            center=map_center,
            zoom=_map_zoom_for_bounds(
                bounds,
                viewport_width_px=GRID_MAP_FALLBACK_VIEWPORT_WIDTH_PX,
                viewport_height_px=GRID_MAP_FALLBACK_VIEWPORT_HEIGHT_PX,
                padding_px=GRID_MAP_STARTUP_FIT_PADDING_PX,
            ),
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
    topology_cache = dict(topology_cache or {})
    topology_revision = topology_cache.get("topology_revision")
    dynamic_revision = 0
    if isinstance(dynamic_payload, dict):
        dynamic_revision = 1
    figure_dict = _build_pandapower_figure_dict(
        topology_cache,
        dynamic_payload,
        title=title,
        uirevision_key=uirevision_key,
        topology_revision=topology_revision,
        dynamic_revision=dynamic_revision,
    )
    return go.Figure(figure_dict)


def build_grid_map_figure_update(
    runtime_state: dict[str, Any],
    current_figure: dict[str, Any] | None,
    *,
    title: str = "Distribution Grid Map",
    uirevision_key: str = "grid-map",
) -> go.Figure | Any | None:
    runtime_state = dict(runtime_state or {})
    topology_cache = runtime_state.get("topology_cache")
    topology_revision = runtime_state.get("topology_revision")
    dynamic_revision = int(runtime_state.get("dynamic_revision", 0) or 0)
    dynamic_payload = runtime_state.get("dynamic_payload")
    initial_figure = runtime_state.get("initial_figure")
    trace_index_meta = runtime_state.get("trace_index_meta")

    current_figure = dict(current_figure or {}) if isinstance(current_figure, dict) else None
    current_meta = dict(((current_figure or {}).get("layout", {}) or {}).get("meta", {}) or {})
    current_topology_revision = current_meta.get("grid_map_topology_revision")
    current_dynamic_revision = int(current_meta.get("grid_map_dynamic_revision", -1) or -1)

    if not isinstance(topology_cache, dict):
        return go.Figure(
            _build_empty_grid_map_figure_dict(
                title=title,
                uirevision_key=uirevision_key,
                topology_revision=topology_revision,
                dynamic_revision=dynamic_revision,
            )
        )

    if current_figure is None or current_topology_revision != topology_revision:
        if str(topology_cache.get("figure_renderer") or "") == "low-trace":
            dynamic_payload_dict = dict(dynamic_payload or {})
            return go.Figure(
                _build_low_trace_figure_dict(
                    topology_cache,
                    _build_dynamic_trace_payload(
                        topology_cache,
                        dict(dynamic_payload_dict.get("bus", {}) or {}),
                        dict(dynamic_payload_dict.get("line", {}) or {}),
                    ),
                    title=title,
                    uirevision_key=uirevision_key,
                    topology_revision=topology_revision,
                    dynamic_revision=dynamic_revision,
                )
            )
        if isinstance(initial_figure, dict) and isinstance(trace_index_meta, list) and trace_index_meta:
            return go.Figure(
                _apply_dynamic_payload_to_figure_dict(
                    initial_figure,
                    topology_cache,
                    trace_index_meta,
                    dynamic_payload,
                    topology_revision=topology_revision,
                    dynamic_revision=dynamic_revision,
                    title=title,
                    uirevision_key=uirevision_key,
                )
            )
        return go.Figure(
            _build_pandapower_figure_dict(
                topology_cache,
                dynamic_payload,
                title=title,
                uirevision_key=uirevision_key,
                topology_revision=topology_revision,
                dynamic_revision=dynamic_revision,
            )
        )

    if current_dynamic_revision == dynamic_revision:
        return None

    if str(topology_cache.get("figure_renderer") or "") == "low-trace":
        patch = _build_low_trace_figure_patch(
            topology_cache,
            dynamic_payload,
            dynamic_revision=dynamic_revision,
        )
        if patch is not None:
            return patch
        dynamic_payload_dict = dict(dynamic_payload or {})
        return go.Figure(
            _build_low_trace_figure_dict(
                topology_cache,
                _build_dynamic_trace_payload(
                    topology_cache,
                    dict(dynamic_payload_dict.get("bus", {}) or {}),
                    dict(dynamic_payload_dict.get("line", {}) or {}),
                ),
                title=title,
                uirevision_key=uirevision_key,
                topology_revision=topology_revision,
                dynamic_revision=dynamic_revision,
            )
        )

    return None


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
    map_background_mode = str(runtime_state.get("map_background_mode") or GRID_MAP_BACKGROUND_MODE_NONE)
    map_background_enabled = bool(runtime_state.get("map_background_enabled", False))
    map_background_reason = str(runtime_state.get("map_background_reason") or "").strip()
    scenario_label = str(runtime_state.get("display_scenario_label") or "").strip()
    input_p = _coerce_float(runtime_state.get("battery_input_p_kw"))
    input_q = _coerce_float(runtime_state.get("battery_input_q_kvar"))
    error_text = str(runtime_state.get("last_error") or "").strip()

    lines = [
        f"Last Success: {last_success} | Input Source: {source} | Stale: {stale}",
        (
            f"Map Mode: {coordinate_mode} | Background: {map_background_mode} | Tiles Enabled: {map_background_enabled} | "
            f"CRS: {source_crs} -> {target_crs}"
        ),
        "Map Refresh: static during stabilization pass | summary values remain live",
        (
            f"Simulation Timestamp: requested={requested} | selected={selected} | "
            f"previous-hour fallback={fallback}"
        ),
        (
            f"LIB Input: P={_format_metric(input_p, decimals=1, unit='kW')} | "
            f"Q={_format_metric(input_q, decimals=1, unit='kvar')}"
        ),
    ]
    if scenario_label:
        lines.insert(1, f"Displayed Scenario: {scenario_label}")
    if map_background_reason:
        lines.append(f"Map Background Reason: {map_background_reason}")
    if error_text:
        lines.append(f"Last Error: {error_text}")
    return lines
