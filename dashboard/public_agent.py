import logging
import math
import os
import threading
import time

import pandas as pd
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, dcc, html
from dash.exceptions import PreventUpdate

from dashboard.history import (
    build_slider_marks,
    clamp_epoch_range,
    load_cropped_measurements_for_range,
    scan_measurement_history_index,
)
from dashboard.plotting import (
    DEFAULT_PLOT_THEME,
    DEFAULT_TRACE_COLORS,
    apply_figure_theme,
    create_plant_figure,
)
from dashboard.ui_state import (
    get_plant_power_toggle_state,
    get_recording_toggle_state,
    is_observed_state_effectively_stale,
    resolve_runtime_transition_state,
)
import scheduling.manual_schedule_manager as msm
from measurement.storage import MEASUREMENT_COLUMNS
from runtime.contracts import sanitize_plant_name
from runtime.paths import get_assets_dir, get_data_dir, get_project_root
from runtime.shared_state import snapshot_locked
from scheduling.runtime import build_effective_schedule_frame
from time_utils import get_config_tz, normalize_datetime_series, normalize_schedule_index, normalize_timestamp_value, now_tz


DEFAULT_PUBLIC_HISTORY_EMPTY_RANGE = [0, 1]


def _binary_toggle_classes(active_side):
    positive = ["toggle-option", "toggle-option--positive"]
    negative = ["toggle-option", "toggle-option--negative"]
    if active_side == "positive":
        positive.append("active")
    elif active_side == "negative":
        negative.append("active")
    return " ".join(positive), " ".join(negative)


def _public_dispatch_toggle_state(dispatch_enabled):
    enabled = bool(dispatch_enabled)
    return {
        "positive_label": "Dispatching" if enabled else "Dispatch",
        "negative_label": "Pause" if enabled else "Paused",
        "active_side": "positive" if enabled else "negative",
    }


def _truncate_text(value, *, max_chars=120):
    text = str(value or "").strip()
    if len(text) <= int(max_chars):
        return text
    return f"{text[: max_chars - 1]}..."


def build_public_history_slice(
    data_dir,
    plant_suffix_by_id,
    *,
    plant_id,
    selected_range,
    tz,
    client_index_data=None,
):
    """
    Build historical selection for public read-only dashboard.

    `client_index_data` is intentionally ignored so public callbacks never trust
    client-provided file metadata or file paths.
    """
    _ = client_index_data
    index_data = scan_measurement_history_index(data_dir, plant_suffix_by_id, tz)
    if not index_data.get("has_data"):
        return {
            "index_data": index_data,
            "selected_range": list(DEFAULT_PUBLIC_HISTORY_EMPTY_RANGE),
            "measurements_df": pd.DataFrame(columns=MEASUREMENT_COLUMNS),
        }

    domain_start = int(index_data["global_start_ms"])
    domain_end = int(index_data["global_end_ms"])
    clamped_range = clamp_epoch_range(selected_range, domain_start, domain_end)
    if not clamped_range:
        clamped_range = [domain_start, domain_end]

    file_meta_list = (index_data.get("files_by_plant", {}) or {}).get(plant_id, [])
    measurements_df = load_cropped_measurements_for_range(
        file_meta_list,
        int(clamped_range[0]),
        int(clamped_range[1]),
        tz,
    )
    return {
        "index_data": index_data,
        "selected_range": clamped_range,
        "measurements_df": measurements_df,
    }


def _apply_basic_auth(app, config):
    auth_mode = str(config.get("DASHBOARD_PUBLIC_READONLY_AUTH_MODE", "basic") or "basic").strip().lower()
    if auth_mode == "none":
        logging.warning("Public read-only dashboard auth mode is 'none'.")
        return
    if auth_mode != "basic":
        raise ValueError(f"Unsupported public read-only auth mode: {auth_mode}")

    username = str(os.getenv("HIL_PUBLIC_DASH_USER", "")).strip()
    password = str(os.getenv("HIL_PUBLIC_DASH_PASS", "")).strip()
    if not username or not password:
        raise RuntimeError(
            "Public read-only dashboard auth mode 'basic' requires HIL_PUBLIC_DASH_USER and HIL_PUBLIC_DASH_PASS env vars."
        )

    try:
        import dash_auth
    except Exception as exc:
        raise RuntimeError(
            "dash-auth package is required for public read-only basic auth. Install dependency 'dash-auth'."
        ) from exc

    dash_auth.BasicAuth(app, {username: password})


def _public_layout(*, plant_name_fn, brand_logo_src, measurement_period_s):
    interval_ms = max(1000, int(float(measurement_period_s) * 1000.0))
    return html.Div(
        className="app-container",
        children=[
            html.Header(
                className="app-header",
                children=[
                    html.Div(
                        className="app-header-brand",
                        children=[
                            html.Img(src=brand_logo_src, alt="i-STENTORE", className="brand-logo"),
                            html.Div(
                                className="app-header-copy",
                                children=[
                                    html.H1("Spanish Demo Dashboard", className="app-title"),
                                    html.P(
                                        "Live status and historical measurement visibility for LIB and VRFB plants.",
                                        className="app-subtitle",
                                    ),
                                ],
                            ),
                        ],
                    )
                ],
            ),
            dcc.Tabs(
                id="public-main-tabs",
                value="status",
                className="main-tabs",
                parent_className="main-tabs-parent",
                children=[
                    dcc.Tab(
                        label="Status",
                        value="status",
                        className="main-tab",
                        selected_className="main-tab--selected",
                        children=[
                            html.Div(
                                className="control-panel",
                                children=[
                                    html.Div(
                                        className="public-indicators-grid",
                                        children=[
                                            html.Div(
                                                id="public-api-connection-indicator",
                                                className="public-indicator",
                                                children=[
                                                    html.Span(className="public-indicator-light"),
                                                    html.Span("API connection", className="public-indicator-text"),
                                                ],
                                            ),
                                            html.Div(
                                                id="public-api-today-indicator",
                                                className="public-indicator",
                                                children=[
                                                    html.Span(className="public-indicator-light"),
                                                    html.Span("Today's Schedule", className="public-indicator-text"),
                                                ],
                                            ),
                                            html.Div(
                                                id="public-api-tomorrow-indicator",
                                                className="public-indicator",
                                                children=[
                                                    html.Span(className="public-indicator-light"),
                                                    html.Span("Tomorrow's Schedule", className="public-indicator-text"),
                                                ],
                                            ),
                                        ],
                                    ),
                                    html.Div(
                                        className="public-meta-row public-meta-inline",
                                        children=[
                                            html.Span(id="public-transport-text", children="Transport: Unknown"),
                                            html.Span(id="public-error-text", children="Error: None"),
                                        ],
                                    ),
                                    html.Div(id="public-plant-summary-table", className="public-summary-table-wrap"),
                                ],
                            ),
                            html.Div(
                                className="plant-card",
                                children=[
                                    html.H3(f"{plant_name_fn('lib')}"),
                                    html.Div(
                                        className="plant-controls-row",
                                        children=[
                                            html.Div(
                                                className="control-group plant-control-group",
                                                children=[
                                                    html.Div(
                                                        className="compact-toggle",
                                                        children=[
                                                            html.Button(
                                                                "Run",
                                                                id="public-start-lib",
                                                                className="toggle-option toggle-option--positive",
                                                                n_clicks=0,
                                                                disabled=True,
                                                            ),
                                                            html.Button(
                                                                "Stopped",
                                                                id="public-stop-lib",
                                                                className="toggle-option toggle-option--negative active",
                                                                n_clicks=0,
                                                                disabled=True,
                                                            ),
                                                        ],
                                                    ),
                                                ],
                                            ),
                                            html.Div(className="control-separator"),
                                            html.Div(
                                                className="control-section",
                                                children=[
                                                    html.Div(
                                                        className="compact-toggle",
                                                        children=[
                                                            html.Button(
                                                                "Dispatch",
                                                                id="public-dispatch-enable-lib",
                                                                className="toggle-option toggle-option--positive",
                                                                n_clicks=0,
                                                                disabled=True,
                                                            ),
                                                            html.Button(
                                                                "Paused",
                                                                id="public-dispatch-disable-lib",
                                                                className="toggle-option toggle-option--negative active",
                                                                n_clicks=0,
                                                                disabled=True,
                                                            ),
                                                        ],
                                                    ),
                                                ],
                                            ),
                                            html.Div(className="control-separator"),
                                            html.Div(
                                                className="control-group record-control-group",
                                                children=[
                                                    html.Div(
                                                        className="compact-toggle",
                                                        children=[
                                                            html.Button(
                                                                "Record",
                                                                id="public-record-lib",
                                                                className="toggle-option toggle-option--positive",
                                                                n_clicks=0,
                                                                disabled=True,
                                                            ),
                                                            html.Button(
                                                                "Stopped",
                                                                id="public-record-stop-lib",
                                                                className="toggle-option toggle-option--negative active",
                                                                n_clicks=0,
                                                                disabled=True,
                                                            ),
                                                        ],
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                    dcc.Graph(id="public-graph-lib", className="plot-graph"),
                                ],
                            ),
                            html.Div(
                                className="plant-card",
                                children=[
                                    html.H3(f"{plant_name_fn('vrfb')}"),
                                    html.Div(
                                        className="plant-controls-row",
                                        children=[
                                            html.Div(
                                                className="control-group plant-control-group",
                                                children=[
                                                    html.Div(
                                                        className="compact-toggle",
                                                        children=[
                                                            html.Button(
                                                                "Run",
                                                                id="public-start-vrfb",
                                                                className="toggle-option toggle-option--positive",
                                                                n_clicks=0,
                                                                disabled=True,
                                                            ),
                                                            html.Button(
                                                                "Stopped",
                                                                id="public-stop-vrfb",
                                                                className="toggle-option toggle-option--negative active",
                                                                n_clicks=0,
                                                                disabled=True,
                                                            ),
                                                        ],
                                                    ),
                                                ],
                                            ),
                                            html.Div(className="control-separator"),
                                            html.Div(
                                                className="control-section",
                                                children=[
                                                    html.Div(
                                                        className="compact-toggle",
                                                        children=[
                                                            html.Button(
                                                                "Dispatch",
                                                                id="public-dispatch-enable-vrfb",
                                                                className="toggle-option toggle-option--positive",
                                                                n_clicks=0,
                                                                disabled=True,
                                                            ),
                                                            html.Button(
                                                                "Paused",
                                                                id="public-dispatch-disable-vrfb",
                                                                className="toggle-option toggle-option--negative active",
                                                                n_clicks=0,
                                                                disabled=True,
                                                            ),
                                                        ],
                                                    ),
                                                ],
                                            ),
                                            html.Div(className="control-separator"),
                                            html.Div(
                                                className="control-group record-control-group",
                                                children=[
                                                    html.Div(
                                                        className="compact-toggle",
                                                        children=[
                                                            html.Button(
                                                                "Record",
                                                                id="public-record-vrfb",
                                                                className="toggle-option toggle-option--positive",
                                                                n_clicks=0,
                                                                disabled=True,
                                                            ),
                                                            html.Button(
                                                                "Stopped",
                                                                id="public-record-stop-vrfb",
                                                                className="toggle-option toggle-option--negative active",
                                                                n_clicks=0,
                                                                disabled=True,
                                                            ),
                                                        ],
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                    dcc.Graph(id="public-graph-vrfb", className="plot-graph"),
                                ],
                            ),
                        ],
                    ),
                    dcc.Tab(
                        label="Plots",
                        value="plots",
                        className="main-tab",
                        selected_className="main-tab--selected",
                        children=[
                            html.Div(
                                className="card",
                                children=[
                                    html.H3("Historical Measurements"),
                                    html.Div(id="public-plots-status-text", className="status-text"),
                                    dcc.Graph(id="public-plots-timeline-graph", className="plot-graph"),
                                    html.Div(id="public-plots-range-label", className="status-text"),
                                    dcc.RangeSlider(
                                        id="public-plots-range-slider",
                                        min=0,
                                        max=1,
                                        value=list(DEFAULT_PUBLIC_HISTORY_EMPTY_RANGE),
                                        marks={},
                                        allowCross=False,
                                        updatemode="mouseup",
                                        disabled=True,
                                    ),
                                ],
                            ),
                            html.Div(
                                className="plant-card",
                                children=[
                                    html.H3(f"{plant_name_fn('lib')}"),
                                    dcc.Graph(id="public-plots-graph-lib", className="plot-graph"),
                                ],
                            ),
                            html.Div(
                                className="plant-card",
                                children=[
                                    html.H3(f"{plant_name_fn('vrfb')}"),
                                    dcc.Graph(id="public-plots-graph-vrfb", className="plot-graph"),
                                ],
                            ),
                        ],
                    ),
                ],
            ),
            dcc.Interval(id="public-interval-component", interval=interval_ms, n_intervals=0),
            dcc.Interval(id="public-plots-refresh-interval", interval=30000, n_intervals=0),
        ],
    )


def build_public_readonly_app(config, shared_data):
    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)

    base_dir = os.path.dirname(__file__)
    project_dir = get_project_root(base_dir)
    assets_dir = get_assets_dir(project_dir)
    data_dir = get_data_dir(project_dir)

    app = Dash(
        __name__,
        suppress_callback_exceptions=True,
        assets_folder=assets_dir,
    )
    _apply_basic_auth(app, config)

    plant_ids = tuple(config.get("PLANT_IDS", ("lib", "vrfb")))
    plants_cfg = config.get("PLANTS", {})
    tz = get_config_tz(config)
    plot_theme = dict(DEFAULT_PLOT_THEME)
    trace_colors = dict(DEFAULT_TRACE_COLORS)

    def plant_name(plant_id):
        return str((plants_cfg.get(plant_id, {}) or {}).get("name", plant_id.upper()))

    def _plant_suffix_by_id():
        return {plant_id: sanitize_plant_name(plant_name(plant_id), plant_id) for plant_id in plant_ids}

    def _manual_status_window_bounds(now_value=None):
        now_value = normalize_timestamp_value(now_value or now_tz(config), tz)
        start = now_value.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + pd.Timedelta(days=2)
        return start, end

    def _voltage_padding_kv_for_plant(plant_id):
        plant_cfg = (plants_cfg.get(plant_id, {}) or {})
        model_cfg = (plant_cfg.get("model", {}) or {})
        try:
            nominal_kv = float(model_cfg.get("poi_voltage_kv"))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(nominal_kv) or nominal_kv <= 0.0 or nominal_kv >= 10.0:
            return None
        return nominal_kv * 0.05

    def _epoch_ms_to_ts(epoch_ms):
        return normalize_timestamp_value(pd.to_datetime(int(epoch_ms), unit="ms", utc=True), tz)

    def _format_epoch_label(epoch_ms):
        ts = _epoch_ms_to_ts(epoch_ms)
        if pd.isna(ts):
            return "n/a"
        return ts.strftime("%Y-%m-%d %H:%M:%S %Z")

    def _empty_history_timeline_figure(message):
        fig = go.Figure()
        fig.add_annotation(text=message, xref="paper", yref="paper", x=0.5, y=0.5, showarrow=False)
        apply_figure_theme(
            fig,
            plot_theme,
            height=160,
            margin=dict(l=40, r=20, t=28, b=22),
            uirevision="public-plots-timeline-empty",
            showlegend=False,
        )
        fig.update_xaxes(title_text="Time")
        fig.update_yaxes(visible=False)
        return fig

    def _build_history_timeline_figure(index_data, selected_range):
        if not isinstance(index_data, dict) or not index_data.get("has_data"):
            return _empty_history_timeline_figure("No historical measurements found.")

        fig = go.Figure()
        color_by_plant = {"lib": trace_colors["api_lib"], "vrfb": trace_colors["api_vrfb"]}
        label_by_plant = {"lib": plant_name("lib"), "vrfb": plant_name("vrfb")}

        for plant_id in plant_ids:
            for item in (index_data.get("files_by_plant", {}) or {}).get(plant_id, []):
                start_ts = _epoch_ms_to_ts(item.get("start_ms"))
                end_ts = _epoch_ms_to_ts(item.get("end_ms"))
                if pd.isna(start_ts) or pd.isna(end_ts):
                    continue
                fig.add_trace(
                    go.Scatter(
                        x=[start_ts, end_ts],
                        y=[label_by_plant.get(plant_id, plant_id.upper())] * 2,
                        mode="lines",
                        line=dict(color=color_by_plant.get(plant_id, plot_theme["muted"]), width=8),
                        name=label_by_plant.get(plant_id, plant_id.upper()),
                        legendgroup=plant_id,
                        showlegend=not any(t.legendgroup == plant_id for t in fig.data),
                        hovertemplate=(
                            f"{label_by_plant.get(plant_id, plant_id.upper())}<br>"
                            f"{os.path.basename(str(item.get('path', '')))}<br>"
                            "Start: %{x|%Y-%m-%d %H:%M:%S}<extra></extra>"
                        ),
                    )
                )

        if selected_range and len(selected_range) == 2:
            try:
                sel_start = _epoch_ms_to_ts(selected_range[0])
                sel_end = _epoch_ms_to_ts(selected_range[1])
                if not pd.isna(sel_start) and not pd.isna(sel_end):
                    fig.add_vrect(
                        x0=min(sel_start, sel_end),
                        x1=max(sel_start, sel_end),
                        fillcolor="#00945a",
                        opacity=0.10,
                        line_width=1,
                        line_color="#00945a",
                    )
            except Exception:
                pass

        if not fig.data:
            return _empty_history_timeline_figure("No historical measurements found.")

        apply_figure_theme(
            fig,
            plot_theme,
            height=160,
            margin=dict(l=50, r=20, t=28, b=22),
            uirevision="public-plots-timeline",
            showlegend=True,
            legend_y=1.04,
        )
        fig.update_xaxes(title_text="Time")
        fig.update_yaxes(title_text="")
        return fig

    def _empty_history_plant_figure(plant_id, message):
        fig = create_plant_figure(
            plant_id,
            plant_name,
            pd.DataFrame(),
            pd.DataFrame(columns=MEASUREMENT_COLUMNS),
            uirevision_key=f"public-plots-empty:{plant_id}",
            tz=tz,
            plot_theme=plot_theme,
            trace_colors=trace_colors,
            voltage_autorange_padding_kv=_voltage_padding_kv_for_plant(plant_id),
        )
        fig.add_annotation(text=message, xref="paper", yref="paper", x=0.5, y=0.5, showarrow=False)
        return fig

    app.layout = _public_layout(
        plant_name_fn=plant_name,
        brand_logo_src=app.get_asset_url("brand/Logotype i-STENTORE.png"),
        measurement_period_s=config.get("MEASUREMENT_PERIOD_S", 5.0),
    )

    @app.callback(
        [
            Output("public-api-connection-indicator", "children"),
            Output("public-api-connection-indicator", "className"),
            Output("public-api-today-indicator", "children"),
            Output("public-api-today-indicator", "className"),
            Output("public-api-tomorrow-indicator", "children"),
            Output("public-api-tomorrow-indicator", "className"),
            Output("public-transport-text", "children"),
            Output("public-error-text", "children"),
            Output("public-plant-summary-table", "children"),
            Output("public-start-lib", "children"),
            Output("public-start-lib", "className"),
            Output("public-stop-lib", "children"),
            Output("public-stop-lib", "className"),
            Output("public-dispatch-enable-lib", "children"),
            Output("public-dispatch-enable-lib", "className"),
            Output("public-dispatch-disable-lib", "children"),
            Output("public-dispatch-disable-lib", "className"),
            Output("public-record-lib", "children"),
            Output("public-record-lib", "className"),
            Output("public-record-stop-lib", "children"),
            Output("public-record-stop-lib", "className"),
            Output("public-start-vrfb", "children"),
            Output("public-start-vrfb", "className"),
            Output("public-stop-vrfb", "children"),
            Output("public-stop-vrfb", "className"),
            Output("public-dispatch-enable-vrfb", "children"),
            Output("public-dispatch-enable-vrfb", "className"),
            Output("public-dispatch-disable-vrfb", "children"),
            Output("public-dispatch-disable-vrfb", "className"),
            Output("public-record-vrfb", "children"),
            Output("public-record-vrfb", "className"),
            Output("public-record-stop-vrfb", "children"),
            Output("public-record-stop-vrfb", "className"),
            Output("public-graph-lib", "figure"),
            Output("public-graph-vrfb", "figure"),
        ],
        [Input("public-interval-component", "n_intervals")],
    )
    def update_public_status_and_graphs(_n_intervals):
        snapshot = snapshot_locked(
            shared_data,
            lambda data: {
                "transport_mode": data.get("transport_mode", "local"),
                "scheduler_running": dict(data.get("scheduler_running_by_plant", {})),
                "transition_by_plant": dict(data.get("plant_transition_by_plant", {})),
                "recording_files": dict(data.get("measurements_filename_by_plant", {})),
                "observed_state_by_plant": dict(data.get("plant_observed_state_by_plant", {})),
                "control_engine_status": dict(data.get("control_engine_status", {})),
                "fetcher_status": dict((data.get("data_fetcher_status", {}) or {})),
                "api_schedule_map": {
                    plant_id: data.get("api_schedule_df_by_plant", {}).get(plant_id, pd.DataFrame()).copy()
                    for plant_id in plant_ids
                },
                "manual_series_map": dict(data.get("manual_schedule_series_df_by_key", {})),
                "manual_merge_enabled": dict(data.get("manual_schedule_merge_enabled_by_key", {})),
                "measurements_map": {
                    plant_id: data.get("current_file_df_by_plant", {}).get(plant_id, pd.DataFrame()).copy()
                    for plant_id in plant_ids
                },
            },
        )

        status_now = now_tz(config)
        enable_state_by_plant = {}
        for plant_id in plant_ids:
            observed = dict(snapshot["observed_state_by_plant"].get(plant_id, {}) or {})
            effective_stale = is_observed_state_effectively_stale(observed, now_ts=status_now)
            enable_state_by_plant[plant_id] = None if effective_stale else observed.get("enable_state")

        runtime_state_by_plant = {}
        for plant_id in plant_ids:
            transition = snapshot["transition_by_plant"].get(plant_id, "unknown")
            runtime_state_by_plant[plant_id] = resolve_runtime_transition_state(
                transition,
                enable_state_by_plant.get(plant_id),
            )

        status = snapshot["fetcher_status"]
        api_connected = bool(status.get("connected"))
        today_available = bool(status.get("today_fetched"))
        tomorrow_available = bool(status.get("tomorrow_fetched"))

        def indicator_payload(label, is_ok):
            class_name = "public-indicator public-indicator--ok" if bool(is_ok) else "public-indicator public-indicator--bad"
            children = [
                html.Span(className="public-indicator-light"),
                html.Span(label, className="public-indicator-text"),
            ]
            return children, class_name

        api_connection_children, api_connection_class = indicator_payload(
            "API connection",
            api_connected,
        )
        api_today_children, api_today_class = indicator_payload(
            "Today's Schedule",
            today_available,
        )
        api_tomorrow_children, api_tomorrow_class = indicator_payload(
            "Tomorrow's Schedule",
            tomorrow_available,
        )

        transport_mode = str(snapshot.get("transport_mode", "local") or "local")
        transport_text = f"Transport: {transport_mode.capitalize()}"

        fetch_error = str(status.get("error") or "").strip()
        control_error = str(snapshot["control_engine_status"].get("last_exception") or "").strip()
        if fetch_error:
            error_text = _truncate_text(fetch_error, max_chars=140)
        elif control_error:
            error_text = _truncate_text(control_error, max_chars=140)
        else:
            error_text = "None"
        error_line = f"Error: {error_text}"

        def _coerce_float(value):
            try:
                number = float(value)
            except (TypeError, ValueError):
                return None
            if not math.isfinite(number):
                return None
            return number

        def _metric_cell(value, unit, *, decimals):
            number = _coerce_float(value)
            if number is None:
                return html.Td("—", className="public-summary-empty public-summary-measurement-cell")
            value_text = f"{number:.{int(decimals)}f}"
            return html.Td(
                html.Div(
                    className="public-summary-metric",
                    children=[
                        html.Span(value_text, className="public-summary-value"),
                        html.Span(unit, className="public-summary-unit"),
                    ],
                )
                ,
                className="public-summary-measurement-cell",
            )

        def _latest_measurements_row(plant_id):
            measurements_df = snapshot["measurements_map"].get(plant_id, pd.DataFrame())
            if not isinstance(measurements_df, pd.DataFrame) or measurements_df.empty:
                return {}
            df_latest = measurements_df.copy()
            if "timestamp" in df_latest.columns:
                df_latest["__ts"] = normalize_datetime_series(df_latest["timestamp"], tz)
                df_latest = df_latest.dropna(subset=["__ts"]).sort_values("__ts")
                if df_latest.empty:
                    return {}
            try:
                return dict(df_latest.iloc[-1].to_dict())
            except Exception:
                return {}

        def _status_chip(plant_id):
            is_running = str(runtime_state_by_plant.get(plant_id, "unknown") or "unknown").lower() == "running"
            return html.Span(
                "Running" if is_running else "Stopped",
                className=(
                    "public-status-chip public-status-chip--running"
                    if is_running
                    else "public-status-chip public-status-chip--stopped"
                ),
            )

        table_rows = []
        for plant_id in plant_ids:
            latest = _latest_measurements_row(plant_id)
            voltage_value = _coerce_float(latest.get("v_poi_kV"))
            voltage_decimals = 3 if voltage_value is not None and abs(voltage_value) < 10.0 else 2
            table_rows.append(
                html.Tr(
                    children=[
                        html.Th(plant_name(plant_id), scope="row", className="public-summary-plant"),
                        html.Td(_status_chip(plant_id), className="public-summary-status-cell"),
                        _metric_cell(latest.get("p_setpoint_kw"), "kW", decimals=1),
                        _metric_cell(latest.get("p_poi_kw"), "kW", decimals=1),
                        _metric_cell(latest.get("q_setpoint_kvar"), "kvar", decimals=1),
                        _metric_cell(latest.get("q_poi_kvar"), "kvar", decimals=1),
                        _metric_cell(latest.get("v_poi_kV"), "kV", decimals=voltage_decimals),
                    ]
                )
            )

        summary_table = html.Table(
            className="public-summary-table",
            children=[
                html.Thead(
                    html.Tr(
                        children=[
                            html.Th("Plant"),
                            html.Th("Status"),
                            html.Th("Pref"),
                            html.Th("P POI"),
                            html.Th("Qref"),
                            html.Th("Q POI"),
                            html.Th("Voltage"),
                        ]
                    )
                ),
                html.Tbody(table_rows),
            ],
        )

        status_window_start, status_window_end = _manual_status_window_bounds(now_value=status_now)

        effective_schedule_map = {}
        for plant_id in plant_ids:
            p_key, q_key = msm.manual_series_keys_for_plant(plant_id)
            effective_schedule_map[plant_id] = build_effective_schedule_frame(
                snapshot["api_schedule_map"].get(plant_id, pd.DataFrame()),
                snapshot["manual_series_map"].get(p_key, pd.DataFrame()),
                snapshot["manual_series_map"].get(q_key, pd.DataFrame()),
                manual_p_enabled=bool(snapshot["manual_merge_enabled"].get(p_key, False)),
                manual_q_enabled=bool(snapshot["manual_merge_enabled"].get(q_key, False)),
                tz=tz,
            )

        def plant_control_labels(plant_id):
            runtime_state = runtime_state_by_plant.get(plant_id, "unknown")
            dispatch_enabled = bool(snapshot["scheduler_running"].get(plant_id, False))
            recording_active = bool(snapshot["recording_files"].get(plant_id))

            power_state = get_plant_power_toggle_state(runtime_state)
            dispatch_state = _public_dispatch_toggle_state(dispatch_enabled)
            record_state = get_recording_toggle_state(recording_active)

            power_classes = _binary_toggle_classes(power_state.get("active_side"))
            dispatch_classes = _binary_toggle_classes(dispatch_state.get("active_side"))
            record_classes = _binary_toggle_classes(record_state.get("active_side"))
            return (
                power_state.get("positive_label", "Run"),
                power_classes[0],
                power_state.get("negative_label", "Stopped"),
                power_classes[1],
                dispatch_state.get("positive_label", "Dispatch"),
                dispatch_classes[0],
                dispatch_state.get("negative_label", "Paused"),
                dispatch_classes[1],
                record_state.get("positive_label", "Record"),
                record_classes[0],
                record_state.get("negative_label", "Stopped"),
                record_classes[1],
            )

        lib_controls = plant_control_labels("lib")
        vrfb_controls = plant_control_labels("vrfb")

        lib_schedule = normalize_schedule_index(effective_schedule_map.get("lib", pd.DataFrame()), tz)
        vrfb_schedule = normalize_schedule_index(effective_schedule_map.get("vrfb", pd.DataFrame()), tz)

        lib_fig = create_plant_figure(
            "lib",
            plant_name,
            lib_schedule,
            snapshot["measurements_map"].get("lib", pd.DataFrame()),
            uirevision_key=f"public-lib:merged:{transport_mode}",
            tz=tz,
            plot_theme=plot_theme,
            trace_colors=trace_colors,
            x_window_start=status_window_start,
            x_window_end=status_window_end,
            time_indicator_ts=status_now,
            voltage_autorange_padding_kv=_voltage_padding_kv_for_plant("lib"),
        )
        vrfb_fig = create_plant_figure(
            "vrfb",
            plant_name,
            vrfb_schedule,
            snapshot["measurements_map"].get("vrfb", pd.DataFrame()),
            uirevision_key=f"public-vrfb:merged:{transport_mode}",
            tz=tz,
            plot_theme=plot_theme,
            trace_colors=trace_colors,
            x_window_start=status_window_start,
            x_window_end=status_window_end,
            time_indicator_ts=status_now,
            voltage_autorange_padding_kv=_voltage_padding_kv_for_plant("vrfb"),
        )

        return (
            api_connection_children,
            api_connection_class,
            api_today_children,
            api_today_class,
            api_tomorrow_children,
            api_tomorrow_class,
            transport_text,
            error_line,
            summary_table,
            lib_controls[0],
            lib_controls[1],
            lib_controls[2],
            lib_controls[3],
            lib_controls[4],
            lib_controls[5],
            lib_controls[6],
            lib_controls[7],
            lib_controls[8],
            lib_controls[9],
            lib_controls[10],
            lib_controls[11],
            vrfb_controls[0],
            vrfb_controls[1],
            vrfb_controls[2],
            vrfb_controls[3],
            vrfb_controls[4],
            vrfb_controls[5],
            vrfb_controls[6],
            vrfb_controls[7],
            vrfb_controls[8],
            vrfb_controls[9],
            vrfb_controls[10],
            vrfb_controls[11],
            lib_fig,
            vrfb_fig,
        )

    @app.callback(
        [
            Output("public-plots-range-slider", "min"),
            Output("public-plots-range-slider", "max"),
            Output("public-plots-range-slider", "value"),
            Output("public-plots-range-slider", "marks"),
            Output("public-plots-range-slider", "disabled"),
            Output("public-plots-status-text", "children"),
            Output("public-plots-range-label", "children"),
            Output("public-plots-timeline-graph", "figure"),
        ],
        [Input("public-main-tabs", "value"), Input("public-plots-refresh-interval", "n_intervals")],
        [State("public-plots-range-slider", "value")],
        prevent_initial_call=False,
    )
    def update_public_historical_range(active_tab, _plots_refresh_n, current_slider_value):
        if active_tab != "plots":
            raise PreventUpdate

        index_data = scan_measurement_history_index(data_dir, _plant_suffix_by_id(), tz)
        if not index_data.get("has_data"):
            return (
                0,
                1,
                list(DEFAULT_PUBLIC_HISTORY_EMPTY_RANGE),
                {},
                True,
                "No measurement files found in data/.",
                "Range: n/a",
                _empty_history_timeline_figure("No historical measurements found."),
            )

        global_start_ms = int(index_data["global_start_ms"])
        global_end_ms = int(index_data["global_end_ms"])
        selected_range = clamp_epoch_range(current_slider_value, global_start_ms, global_end_ms)
        if not selected_range:
            selected_range = [global_start_ms, global_end_ms]

        slider_min = global_start_ms
        slider_max = global_end_ms if global_end_ms > global_start_ms else global_start_ms + 1
        slider_marks = build_slider_marks(slider_min, slider_max, tz, max_marks=8)

        files_by_plant = index_data.get("files_by_plant", {}) or {}
        status_text = (
            f"Historical files loaded: {plant_name('lib')}={len(files_by_plant.get('lib', []))} "
            f"{plant_name('vrfb')}={len(files_by_plant.get('vrfb', []))} | "
            f"Detected range: {_format_epoch_label(global_start_ms)} -> {_format_epoch_label(global_end_ms)}"
        )
        range_label = f"Range: {_format_epoch_label(selected_range[0])} -> {_format_epoch_label(selected_range[1])}"
        timeline_fig = _build_history_timeline_figure(index_data, selected_range)

        return (
            slider_min,
            slider_max,
            selected_range,
            slider_marks,
            False,
            status_text,
            range_label,
            timeline_fig,
        )

    @app.callback(
        [Output("public-plots-graph-lib", "figure"), Output("public-plots-graph-vrfb", "figure")],
        [
            Input("public-main-tabs", "value"),
            Input("public-plots-range-slider", "value"),
            Input("public-plots-refresh-interval", "n_intervals"),
        ],
        prevent_initial_call=False,
    )
    def update_public_historical_plots(active_tab, selected_range, _plots_refresh_n):
        if active_tab != "plots":
            raise PreventUpdate

        suffix_map = _plant_suffix_by_id()
        lib_slice = build_public_history_slice(
            data_dir,
            suffix_map,
            plant_id="lib",
            selected_range=selected_range,
            tz=tz,
        )
        vrfb_slice = build_public_history_slice(
            data_dir,
            suffix_map,
            plant_id="vrfb",
            selected_range=selected_range,
            tz=tz,
        )

        lib_index = lib_slice.get("index_data", {}) or {}
        vrfb_index = vrfb_slice.get("index_data", {}) or {}
        if not lib_index.get("has_data") and not vrfb_index.get("has_data"):
            return (
                _empty_history_plant_figure("lib", "No historical LIB measurements found."),
                _empty_history_plant_figure("vrfb", "No historical VRFB measurements found."),
            )

        lib_measurements = lib_slice.get("measurements_df", pd.DataFrame())
        vrfb_measurements = vrfb_slice.get("measurements_df", pd.DataFrame())

        if lib_measurements.empty:
            lib_fig = _empty_history_plant_figure("lib", f"No {plant_name('lib')} data in selected range.")
        else:
            lib_range = list(lib_slice.get("selected_range") or DEFAULT_PUBLIC_HISTORY_EMPTY_RANGE)
            lib_fig = create_plant_figure(
                "lib",
                plant_name,
                pd.DataFrame(),
                lib_measurements,
                uirevision_key=f"public-plots:lib:{lib_range[0]}:{lib_range[1]}",
                tz=tz,
                plot_theme=plot_theme,
                trace_colors=trace_colors,
                voltage_autorange_padding_kv=_voltage_padding_kv_for_plant("lib"),
            )

        if vrfb_measurements.empty:
            vrfb_fig = _empty_history_plant_figure("vrfb", f"No {plant_name('vrfb')} data in selected range.")
        else:
            vrfb_range = list(vrfb_slice.get("selected_range") or DEFAULT_PUBLIC_HISTORY_EMPTY_RANGE)
            vrfb_fig = create_plant_figure(
                "vrfb",
                plant_name,
                pd.DataFrame(),
                vrfb_measurements,
                uirevision_key=f"public-plots:vrfb:{vrfb_range[0]}:{vrfb_range[1]}",
                tz=tz,
                plot_theme=plot_theme,
                trace_colors=trace_colors,
                voltage_autorange_padding_kv=_voltage_padding_kv_for_plant("vrfb"),
            )

        return lib_fig, vrfb_fig

    return app


def public_dashboard_agent(config, shared_data):
    """Run a separate public read-only dashboard instance."""
    logging.info("Public read-only dashboard agent starting.")
    try:
        app = build_public_readonly_app(config, shared_data)
    except Exception as exc:
        logging.error("Public read-only dashboard initialization failed: %s", exc)
        return

    dashboard_host = str(config.get("DASHBOARD_PUBLIC_READONLY_HOST", "127.0.0.1"))
    dashboard_port = int(config.get("DASHBOARD_PUBLIC_READONLY_PORT", 8060))

    def run_app():
        app.run(host=dashboard_host, port=dashboard_port, debug=False, threaded=True)

    thread = threading.Thread(target=run_app, daemon=True)
    thread.start()

    while not shared_data["shutdown_event"].is_set():
        time.sleep(1)

    logging.info("Public read-only dashboard agent stopped.")


if __name__ == "__main__":
    pass
