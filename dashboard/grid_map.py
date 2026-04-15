"""Shared dashboard helpers for the Grid Map page."""

from __future__ import annotations

import math
import time

from dash import dcc, html

GRID_MAP_INTERACTION_PAUSE_WINDOW_S = 5.0
GRID_MAP_SCENARIO_WITH_BATTERY = "with_battery"
GRID_MAP_SCENARIO_WITHOUT_BATTERY = "without_battery"
GRID_MAP_SCENARIO_LABELS = {
    GRID_MAP_SCENARIO_WITH_BATTERY: "With Battery",
    GRID_MAP_SCENARIO_WITHOUT_BATTERY: "Without Battery",
}


def build_grid_map_page(*, prefix: str, title: str = "Grid Map"):
    graph_id = f"{prefix}-grid-map-figure" if prefix else "grid-map-figure"
    render_state_id = f"{prefix}-grid-map-render-state" if prefix else "grid-map-render-state"
    interaction_state_id = f"{prefix}-grid-map-interaction-state" if prefix else "grid-map-interaction-state"
    startup_fit_state_id = f"{prefix}-grid-map-startup-fit-state" if prefix else "grid-map-startup-fit-state"
    scenario_toggle_id = f"{prefix}-grid-map-scenario-toggle" if prefix else "grid-map-scenario-toggle"
    summary_id = f"{prefix}-grid-map-summary" if prefix else "grid-map-summary"
    meta_id = f"{prefix}-grid-map-meta" if prefix else "grid-map-meta"
    status_id = f"{prefix}-grid-map-status" if prefix else "grid-map-status"
    return html.Div(
        className="card",
        children=[
            dcc.Store(id=render_state_id, data=None),
            dcc.Store(id=interaction_state_id, data=None),
            dcc.Store(id=startup_fit_state_id, data=None),
            html.H3(title),
            html.Div(id=summary_id, className="grid-map-summary-grid"),
            html.Div(
                className="grid-map-toggle-row",
                children=[
                    dcc.Checklist(
                        id=scenario_toggle_id,
                        className="grid-map-scenario-toggle",
                        options=[{"label": "No Battery", "value": GRID_MAP_SCENARIO_WITHOUT_BATTERY}],
                        value=[],
                        inline=True,
                    ),
                ],
            ),
            dcc.Graph(id=graph_id, className="plot-graph grid-map-graph"),
            html.Div(
                className="grid-map-meta-block",
                children=[
                    html.Div(id=status_id, className="status-text"),
                    html.Div(id=meta_id),
                ],
            ),
        ],
    )


def _coerce_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _metric_card(title, value_text, status_modifier="normal"):
    class_name = "grid-map-summary-card"
    if status_modifier and status_modifier != "normal":
        class_name = f"{class_name} grid-map-summary-card--{status_modifier}"
    return html.Div(
        className=class_name,
        children=[
            html.Div(title, className="grid-map-summary-label"),
            html.Div(value_text, className="grid-map-summary-value"),
        ],
    )


def build_grid_map_summary_cards(summary):
    summary = dict(summary or {})
    battery_voltage = _coerce_float(summary.get("battery_voltage_pu"))
    min_voltage = _coerce_float(summary.get("min_voltage_pu"))
    max_voltage = _coerce_float(summary.get("max_voltage_pu"))
    voltage_violations = int(summary.get("num_voltage_violations", 0) or 0)
    max_line_loading = _coerce_float(summary.get("max_line_loading_pct"))
    overloaded_lines = int(summary.get("num_overloaded_lines", 0) or 0)

    return [
        _metric_card("Battery Voltage", "n/a" if battery_voltage is None else f"{battery_voltage:.4f} pu"),
        _metric_card("Lowest Voltage", "n/a" if min_voltage is None else f"{min_voltage:.4f} pu", "alert" if min_voltage is not None and min_voltage < 0.95 else "normal"),
        _metric_card("Highest Voltage", "n/a" if max_voltage is None else f"{max_voltage:.4f} pu", "alert" if max_voltage is not None and max_voltage > 1.05 else "normal"),
        _metric_card("Out-of-Range Buses", f"{voltage_violations}", "alert" if voltage_violations > 0 else "ok"),
        _metric_card("Highest Line Loading", "n/a" if max_line_loading is None else f"{max_line_loading:.1f}%", "alert" if max_line_loading is not None and max_line_loading > 100.0 else "normal"),
        _metric_card("Overloaded Lines", f"{overloaded_lines}", "alert" if overloaded_lines > 0 else "ok"),
    ]


def resolve_grid_map_scenario_key(toggle_values):
    selected_values = set(str(value) for value in list(toggle_values or []))
    if GRID_MAP_SCENARIO_WITHOUT_BATTERY in selected_values:
        return GRID_MAP_SCENARIO_WITHOUT_BATTERY
    return GRID_MAP_SCENARIO_WITH_BATTERY


def build_grid_map_display_runtime(runtime_state, scenario_key):
    runtime = dict(runtime_state or {})
    scenario_key = str(scenario_key or GRID_MAP_SCENARIO_WITH_BATTERY)
    scenario_results = dict(runtime.get("scenario_results", {}) or {})
    scenario_entry = dict(scenario_results.get(scenario_key, {}) or {})
    if scenario_key == GRID_MAP_SCENARIO_WITH_BATTERY and not scenario_entry:
        scenario_entry = {
            "requested_timestamp_local": runtime.get("requested_timestamp_local"),
            "selected_timestamp_local": runtime.get("selected_timestamp_local"),
            "selected_timestamp_utc": runtime.get("selected_timestamp_utc"),
            "used_previous_hour_fallback": runtime.get("used_previous_hour_fallback"),
            "battery_input_p_kw": runtime.get("battery_input_p_kw"),
            "battery_input_q_kvar": runtime.get("battery_input_q_kvar"),
            "battery_input_p_mw": runtime.get("battery_input_p_mw"),
            "battery_input_q_mvar": runtime.get("battery_input_q_mvar"),
            "summary": runtime.get("summary"),
            "dynamic_payload": runtime.get("dynamic_payload"),
        }

    for key in (
        "requested_timestamp_local",
        "selected_timestamp_local",
        "selected_timestamp_utc",
        "used_previous_hour_fallback",
        "battery_input_p_kw",
        "battery_input_q_kvar",
        "battery_input_p_mw",
        "battery_input_q_mvar",
        "summary",
        "dynamic_payload",
    ):
        if key in scenario_entry:
            runtime[key] = scenario_entry.get(key)

    runtime["display_scenario_key"] = scenario_key
    runtime["display_scenario_label"] = GRID_MAP_SCENARIO_LABELS.get(scenario_key, scenario_key)
    return runtime


def build_grid_map_meta_children(lines):
    return [html.Div(str(line), className="status-text") for line in list(lines or [])]


def register_grid_map_interaction(relayout_data, *, now_s=None):
    if not isinstance(relayout_data, dict) or not relayout_data:
        return None
    interaction_time_s = float(time.time() if now_s is None else now_s)
    return {
        "last_interaction_at_s": interaction_time_s,
        "last_relayout_keys": sorted(str(key) for key in relayout_data.keys()),
    }


def is_grid_map_refresh_paused(interaction_state, *, now_s=None, pause_window_s=GRID_MAP_INTERACTION_PAUSE_WINDOW_S):
    if not isinstance(interaction_state, dict):
        return False
    try:
        last_interaction_at_s = float(interaction_state.get("last_interaction_at_s"))
    except (TypeError, ValueError):
        return False
    current_time_s = float(time.time() if now_s is None else now_s)
    try:
        pause_window = float(pause_window_s)
    except (TypeError, ValueError):
        pause_window = GRID_MAP_INTERACTION_PAUSE_WINDOW_S
    if pause_window <= 0.0:
        return False
    return (current_time_s - last_interaction_at_s) < pause_window


def build_grid_map_status_text(runtime_state):
    runtime_state = dict(runtime_state or {})
    state = str(runtime_state.get("state") or "idle")
    topology_ready = bool(runtime_state.get("topology_ready", False))
    stale = bool(runtime_state.get("stale", True))
    coordinate_mode = str(runtime_state.get("coordinate_mode") or "schematic")
    map_background_mode = str(runtime_state.get("map_background_mode") or "none")
    refresh_paused = bool(runtime_state.get("refresh_paused", False))
    display_scenario_label = str(runtime_state.get("display_scenario_label") or "").strip()
    error_text = str(runtime_state.get("last_error") or runtime_state.get("topology_error") or "").strip()
    status_text = (
        f"Grid Map Runtime: state={state} | topology_ready={topology_ready} | "
        f"stale={stale} | mode={coordinate_mode} | background={map_background_mode} | "
        f"map_refresh={'paused' if refresh_paused else 'live'}"
    )
    if display_scenario_label:
        status_text += f" | scenario={display_scenario_label}"
    if error_text:
        status_text += f" | error={error_text}"
    return status_text


def grid_map_startup_fit_clientside_js(graph_id: str) -> str:
    return f"""
    function(renderState, currentFigure, fitState) {{
        var noUpdate = (window.dash_clientside && window.dash_clientside.no_update)
            ? window.dash_clientside.no_update
            : null;
        if (!renderState || !currentFigure || !currentFigure.layout) {{
            return [noUpdate, noUpdate];
        }}
        var layout = currentFigure.layout || {{}};
        var meta = layout.meta || {{}};
        var topologyRevision = meta.grid_map_topology_revision;
        if (topologyRevision === undefined || topologyRevision === null) {{
            topologyRevision = renderState.grid_map_topology_revision;
        }}
        if (!topologyRevision) {{
            return [noUpdate, noUpdate];
        }}
        if (fitState && fitState.last_topology_revision === topologyRevision) {{
            return [noUpdate, noUpdate];
        }}
        if ((meta.grid_map_coordinate_mode || "schematic") !== "geographic") {{
            return [noUpdate, {{
                last_topology_revision: topologyRevision,
                coordinate_mode: meta.grid_map_coordinate_mode || "schematic",
                applied: false
            }}];
        }}
        var fitBounds = meta.grid_map_fit_bounds || null;
        if (!fitBounds) {{
            return [noUpdate, noUpdate];
        }}
        var west = Number(fitBounds.west);
        var east = Number(fitBounds.east);
        var south = Number(fitBounds.south);
        var north = Number(fitBounds.north);
        if (![west, east, south, north].every(Number.isFinite)) {{
            return [noUpdate, noUpdate];
        }}
        var wrapper = document.getElementById("{graph_id}");
        var gd = wrapper ? (wrapper.querySelector(".js-plotly-plot") || wrapper) : null;
        if (!gd) {{
            return [noUpdate, noUpdate];
        }}
        var margin = layout.margin || {{}};
        var graphWidth = gd.clientWidth || gd.offsetWidth || 0;
        var graphHeight = gd.clientHeight || gd.offsetHeight || Number(layout.height || 0);
        var viewportWidth = Math.max(
            1,
            graphWidth - Number(margin.l || 0) - Number(margin.r || 0)
        );
        var viewportHeight = Math.max(
            1,
            graphHeight - Number(margin.t || 0) - Number(margin.b || 0)
        );
        var padding = Math.max(0, Number(meta.grid_map_fit_padding_px || 32));
        var availableWidth = Math.max(1, viewportWidth - (2 * padding));
        var availableHeight = Math.max(1, viewportHeight - (2 * padding));

        function mercatorY(lat) {{
            var clipped = Math.max(-85.05112878, Math.min(85.05112878, Number(lat)));
            var sin = Math.sin(clipped * Math.PI / 180);
            sin = Math.max(-0.9999, Math.min(0.9999, sin));
            return 0.5 - (Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI));
        }}

        var lonFraction = Math.abs(east - west) / 360.0;
        var latFraction = Math.abs(mercatorY(north) - mercatorY(south));
        var zoom;
        if (lonFraction < 1e-12 && latFraction < 1e-12) {{
            zoom = 20.0;
        }} else {{
            var tileSize = 512.0;
            var zoomX = lonFraction < 1e-12
                ? Infinity
                : Math.log2(availableWidth / (tileSize * lonFraction));
            var zoomY = latFraction < 1e-12
                ? Infinity
                : Math.log2(availableHeight / (tileSize * latFraction));
            zoom = Math.min(zoomX, zoomY);
            if (!Number.isFinite(zoom)) {{
                zoom = 20.0;
            }}
            zoom = Math.max(0.0, Math.min(24.0, zoom));
        }}

        var nextFigure = JSON.parse(JSON.stringify(currentFigure));
        nextFigure.layout = nextFigure.layout || {{}};
        nextFigure.layout.map = nextFigure.layout.map || {{}};
        nextFigure.layout.map.center = {{
            lon: (west + east) / 2.0,
            lat: (south + north) / 2.0
        }};
        nextFigure.layout.map.zoom = zoom;
        return [nextFigure, {{
            last_topology_revision: topologyRevision,
            coordinate_mode: "geographic",
            applied: true,
            zoom: zoom
        }}];
    }}
    """
