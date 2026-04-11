"""Shared dashboard helpers for the Grid Map page."""

from __future__ import annotations

import math
import time

from dash import dcc, html

GRID_MAP_INTERACTION_PAUSE_WINDOW_S = 5.0


def build_grid_map_page(*, prefix: str, title: str = "Grid Map"):
    graph_id = f"{prefix}-grid-map-figure" if prefix else "grid-map-figure"
    render_state_id = f"{prefix}-grid-map-render-state" if prefix else "grid-map-render-state"
    interaction_state_id = f"{prefix}-grid-map-interaction-state" if prefix else "grid-map-interaction-state"
    summary_id = f"{prefix}-grid-map-summary" if prefix else "grid-map-summary"
    meta_id = f"{prefix}-grid-map-meta" if prefix else "grid-map-meta"
    status_id = f"{prefix}-grid-map-status" if prefix else "grid-map-status"
    return html.Div(
        className="card",
        children=[
            dcc.Store(id=render_state_id, data=None),
            dcc.Store(id=interaction_state_id, data=None),
            html.Div(id=summary_id, className="grid-map-summary-grid"),
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
    error_text = str(runtime_state.get("last_error") or runtime_state.get("topology_error") or "").strip()
    status_text = (
        f"Grid Map Runtime: state={state} | topology_ready={topology_ready} | "
        f"stale={stale} | mode={coordinate_mode} | background={map_background_mode} | "
        f"map_refresh={'paused' if refresh_paused else 'live'}"
    )
    if error_text:
        status_text += f" | error={error_text}"
    return status_text
