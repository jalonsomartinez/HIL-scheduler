"""Shared dashboard helpers for the Grid Map page."""

from __future__ import annotations

import math

from dash import dcc, html


def build_grid_map_page(*, prefix: str, title: str = "Grid Map"):
    graph_id = f"{prefix}-grid-map-figure" if prefix else "grid-map-figure"
    render_state_id = f"{prefix}-grid-map-render-state" if prefix else "grid-map-render-state"
    summary_id = f"{prefix}-grid-map-summary" if prefix else "grid-map-summary"
    meta_id = f"{prefix}-grid-map-meta" if prefix else "grid-map-meta"
    status_id = f"{prefix}-grid-map-status" if prefix else "grid-map-status"
    return html.Div(
        className="card",
        children=[
            html.H3(title),
            dcc.Store(id=render_state_id, data=None),
            html.Div(id=status_id, className="status-text"),
            html.Div(id=summary_id, className="grid-map-summary-grid"),
            html.Div(id=meta_id, className="grid-map-meta-block"),
            dcc.Graph(id=graph_id, className="plot-graph grid-map-graph"),
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
    min_voltage = _coerce_float(summary.get("min_voltage_pu"))
    max_voltage = _coerce_float(summary.get("max_voltage_pu"))
    voltage_violations = int(summary.get("num_voltage_violations", 0) or 0)
    max_line_loading = _coerce_float(summary.get("max_line_loading_pct"))
    overloaded_lines = int(summary.get("num_overloaded_lines", 0) or 0)

    return [
        _metric_card("Lowest Voltage", "n/a" if min_voltage is None else f"{min_voltage:.4f} pu", "alert" if min_voltage is not None and min_voltage < 0.95 else "normal"),
        _metric_card("Highest Voltage", "n/a" if max_voltage is None else f"{max_voltage:.4f} pu", "alert" if max_voltage is not None and max_voltage > 1.05 else "normal"),
        _metric_card("Out-of-Range Buses", f"{voltage_violations}", "alert" if voltage_violations > 0 else "ok"),
        _metric_card("Highest Line Loading", "n/a" if max_line_loading is None else f"{max_line_loading:.1f}%", "alert" if max_line_loading is not None and max_line_loading > 100.0 else "normal"),
        _metric_card("Overloaded Lines", f"{overloaded_lines}", "alert" if overloaded_lines > 0 else "ok"),
    ]


def build_grid_map_meta_children(lines):
    return [html.Div(str(line), className="status-text") for line in list(lines or [])]


def build_grid_map_status_text(runtime_state):
    runtime_state = dict(runtime_state or {})
    state = str(runtime_state.get("state") or "idle")
    topology_ready = bool(runtime_state.get("topology_ready", False))
    stale = bool(runtime_state.get("stale", True))
    coordinate_mode = str(runtime_state.get("coordinate_mode") or "schematic")
    map_background_enabled = bool(runtime_state.get("map_background_enabled", False))
    error_text = str(runtime_state.get("last_error") or runtime_state.get("topology_error") or "").strip()
    status_text = (
        f"Grid Map Runtime: state={state} | topology_ready={topology_ready} | "
        f"stale={stale} | mode={coordinate_mode} | basemap={map_background_enabled} | map_refresh=static"
    )
    if error_text:
        status_text += f" | error={error_text}"
    return status_text
