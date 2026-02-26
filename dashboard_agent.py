import base64
import io
import json
import logging
import math
import os
import threading
import time
from datetime import datetime, timedelta

import dash
import pandas as pd
import plotly.graph_objects as go
from dash import ALL, Dash, Input, Output, State, callback_context, dcc, html
from dash.exceptions import PreventUpdate

from control_command_runtime import enqueue_control_command
from dashboard_command_intents import command_intent_from_control_trigger, transport_switch_intent_from_confirm
from dashboard_settings_intents import (
    api_connection_intent_from_trigger,
    manual_settings_intent_from_trigger,
    posting_intent_from_trigger,
)
from dashboard_control_health import (
    summarize_control_engine_status,
    summarize_control_queue_status,
    summarize_plant_modbus_health,
)
from dashboard_history import (
    build_slider_marks,
    clamp_epoch_range,
    load_cropped_measurements_for_range,
    scan_measurement_history_index,
    serialize_measurements_for_download,
)
from dashboard_layout import build_dashboard_layout
from dashboard_logs import get_logs_dir, get_today_log_file_path, parse_and_format_historical_logs, read_log_tail
from dashboard_plotting import (
    DEFAULT_PLOT_THEME,
    DEFAULT_TRACE_COLORS,
    apply_figure_theme,
    create_plant_figure,
    create_manual_series_figure,
)
from dashboard_ui_state import (
    get_plant_control_labels_and_disabled,
    resolve_click_feedback_transition_state,
    resolve_runtime_transition_state,
)
from dashboard_settings_ui_state import (
    api_connection_controls_state,
    api_connection_display_state,
    manual_series_controls_state,
    manual_series_display_state,
    posting_controls_state,
    posting_display_state,
    resolve_command_click_feedback_state,
)
import manual_schedule_manager as msm
from measurement_storage import MEASUREMENT_COLUMNS
from runtime_contracts import sanitize_plant_name
from schedule_runtime import build_effective_schedule_frame
from settings_command_runtime import enqueue_settings_command
from shared_state import snapshot_locked
from time_utils import get_config_tz, normalize_datetime_series, normalize_schedule_index, normalize_timestamp_value, now_tz


def dashboard_agent(config, shared_data):
    """Dash dashboard with global source/transport and per-plant controls/plots."""
    logging.info("Dashboard agent started.")

    log = logging.getLogger("werkzeug")
    log.setLevel(logging.ERROR)

    app = Dash(__name__, suppress_callback_exceptions=True)

    plant_ids = tuple(config.get("PLANT_IDS", ("lib", "vrfb")))
    plants_cfg = config.get("PLANTS", {})
    tz = get_config_tz(config)

    initial_snapshot = snapshot_locked(
        shared_data,
        lambda data: {
            "initial_transport": data.get("transport_mode", "local"),
            "initial_posting_enabled": bool(
                (data.get("posting_runtime", {}) or {}).get(
                    "policy_enabled",
                    config.get("ISTENTORE_POST_MEASUREMENTS_IN_API_MODE", True),
                )
            ),
        },
    )
    initial_transport = initial_snapshot["initial_transport"]
    initial_posting_enabled = bool(initial_snapshot["initial_posting_enabled"])

    brand_logo_src = app.get_asset_url("brand/Logotype i-STENTORE.png")

    plot_theme = dict(DEFAULT_PLOT_THEME)
    trace_colors = dict(DEFAULT_TRACE_COLORS)
    base_dir = os.path.dirname(__file__)
    ui_transition_feedback_hold_s = 2.0

    def plant_name(plant_id):
        return str((plants_cfg.get(plant_id, {}) or {}).get("name", plant_id.upper()))

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

    def _manual_window_bounds(now_value=None):
        now_value = normalize_timestamp_value(now_value or now_tz(config), tz)
        start = now_value.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + pd.Timedelta(days=2)
        return start, end

    def _sync_manual_draft_shared_state_locked(data, *, now_value=None):
        manual_series_map = dict(data.get("manual_schedule_draft_series_df_by_key", {}))
        for key in msm.MANUAL_SERIES_KEYS:
            manual_series_map.setdefault(key, pd.DataFrame(columns=["setpoint"]))
        window_start, window_end = _manual_window_bounds(now_value=now_value)
        pruned_series_map = msm.prune_manual_series_map_to_window(manual_series_map, tz, window_start, window_end)
        data["manual_schedule_draft_series_df_by_key"] = pruned_series_map

    def _set_manual_series_from_editor(series_key, rows, start_dt):
        if series_key not in msm.MANUAL_SERIES_KEYS:
            raise ValueError("Invalid manual schedule selector")
        series_df = msm.manual_editor_rows_to_series_df(rows, start_dt, timezone_name=config.get("TIMEZONE_NAME"))
        with shared_data["lock"]:
            _sync_manual_draft_shared_state_locked(shared_data)
            series_map = dict(shared_data.get("manual_schedule_draft_series_df_by_key", {}))
            series_map[series_key] = series_df
            shared_data["manual_schedule_draft_series_df_by_key"] = series_map
            _sync_manual_draft_shared_state_locked(shared_data)
        return series_df

    def _get_manual_series_snapshot():
        return snapshot_locked(
            shared_data,
            lambda data: {
                "draft_series_map": dict(data.get("manual_schedule_draft_series_df_by_key", {})),
                "runtime_state": dict(data.get("manual_series_runtime_state_by_key", {})),
                "applied_series_map": dict(data.get("manual_schedule_series_df_by_key", {})),
                "merge_enabled": dict(data.get("manual_schedule_merge_enabled_by_key", {})),
            },
        )

    def _manual_series_is_dirty(series_key, draft_df, applied_df):
        try:
            draft_norm = msm.normalize_manual_series_df(draft_df, timezone_name=config.get("TIMEZONE_NAME"))
            applied_norm = msm.normalize_manual_series_df(applied_df, timezone_name=config.get("TIMEZONE_NAME"))
            return not draft_norm.equals(applied_norm)
        except Exception:
            return True

    def _editor_start_to_datetime(date_value, hour_value, minute_value, second_value):
        if not date_value:
            raise ValueError("Start date is required")
        try:
            hour = int(hour_value or 0)
            minute = int(minute_value or 0)
            second = int(second_value or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("Start time fields must be integers") from exc
        if hour < 0 or hour > 23 or minute < 0 or minute > 59 or second < 0 or second > 59:
            raise ValueError("Start time is out of range")
        start_dt = pd.Timestamp(date_value).replace(hour=hour, minute=minute, second=second, microsecond=0)
        return normalize_timestamp_value(start_dt, tz)

    def _parse_trigger_id(prop_id):
        if not prop_id:
            return None
        raw = str(prop_id).split(".")[0]
        if raw.startswith("{") and raw.endswith("}"):
            try:
                return json.loads(raw)
            except Exception:
                return raw
        return raw

    def _command_status_action_token(status):
        return f"{status.get('kind')}:{status.get('id')}:{status.get('state')}"

    def _enqueue_dashboard_control_intent(intent, *, trigger_id=None):
        status = enqueue_control_command(
            shared_data,
            kind=intent["kind"],
            payload=intent["payload"],
            source="dashboard",
            now_fn=lambda: now_tz(config),
        )
        if trigger_id is None:
            logging.info(
                "Dashboard: queued control command kind=%s id=%s state=%s",
                status.get("kind"),
                status.get("id"),
                status.get("state"),
            )
        else:
            logging.info(
                "Dashboard: queued command trigger=%s kind=%s id=%s state=%s",
                trigger_id,
                status.get("kind"),
                status.get("id"),
                status.get("state"),
            )
        return status

    def _enqueue_dashboard_settings_intent(intent, *, trigger_id, log_label="settings command"):
        status = enqueue_settings_command(
            shared_data,
            kind=intent["kind"],
            payload=intent["payload"],
            source="dashboard",
            now_fn=lambda: now_tz(config),
        )
        logging.info(
            "Dashboard: queued %s trigger=%s kind=%s id=%s state=%s",
            log_label,
            trigger_id,
            status.get("kind"),
            status.get("id"),
            status.get("state"),
        )
        return status

    def _manual_toggle_classes(enabled):
        if bool(enabled):
            return "toggle-option active", "toggle-option"
        return "toggle-option", "toggle-option active"

    def _epoch_ms_to_ts(epoch_ms):
        return normalize_timestamp_value(pd.to_datetime(int(epoch_ms), unit="ms", utc=True), tz)

    def _format_epoch_label(epoch_ms):
        ts = _epoch_ms_to_ts(epoch_ms)
        if pd.isna(ts):
            return "n/a"
        return ts.strftime("%Y-%m-%d %H:%M:%S %Z")

    def _range_meta_for_selection(selected_range):
        if not selected_range or len(selected_range) != 2:
            return None
        start_ms = int(selected_range[0])
        end_ms = int(selected_range[1])
        start_ts = _epoch_ms_to_ts(start_ms)
        end_ts = _epoch_ms_to_ts(end_ms)
        if pd.isna(start_ts) or pd.isna(end_ts):
            return None
        return {
            "start_ms": start_ms,
            "end_ms": end_ms,
            "start_iso": start_ts.isoformat(),
            "end_iso": end_ts.isoformat(),
            "start_token": start_ts.strftime("%Y%m%dT%H%M%S%z"),
            "end_token": end_ts.strftime("%Y%m%dT%H%M%S%z"),
        }

    def _empty_history_timeline_figure(message):
        fig = go.Figure()
        fig.add_annotation(text=message, xref="paper", yref="paper", x=0.5, y=0.5, showarrow=False)
        apply_figure_theme(
            fig,
            plot_theme,
            height=160,
            margin=dict(l=40, r=20, t=28, b=22),
            uirevision="plots-timeline-empty",
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
            uirevision="plots-timeline",
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
            uirevision_key=f"plots-empty:{plant_id}",
            tz=tz,
            plot_theme=plot_theme,
            trace_colors=trace_colors,
            voltage_autorange_padding_kv=_voltage_padding_kv_for_plant(plant_id),
        )
        fig.add_annotation(text=message, xref="paper", yref="paper", x=0.5, y=0.5, showarrow=False)
        return fig

    def _load_history_df_for_plant(index_data, plant_id, selected_range):
        if not isinstance(index_data, dict) or not index_data.get("has_data"):
            return pd.DataFrame(columns=MEASUREMENT_COLUMNS)
        if not selected_range or len(selected_range) != 2:
            return pd.DataFrame(columns=MEASUREMENT_COLUMNS)
        file_meta_list = (index_data.get("files_by_plant", {}) or {}).get(plant_id, [])
        if not file_meta_list:
            return pd.DataFrame(columns=MEASUREMENT_COLUMNS)
        try:
            start_ms = int(selected_range[0])
            end_ms = int(selected_range[1])
        except (TypeError, ValueError):
            return pd.DataFrame(columns=MEASUREMENT_COLUMNS)
        return load_cropped_measurements_for_range(file_meta_list, start_ms, end_ms, tz)

    def resolve_runtime_transition(plant_id, transition_state, enable_state):
        resolved = resolve_runtime_transition_state(transition_state, enable_state)

        if resolved != transition_state:
            with shared_data["lock"]:
                shared_data["plant_transition_by_plant"][plant_id] = resolved
        return resolved

    with shared_data["lock"]:
        if "manual_schedule_draft_series_df_by_key" not in shared_data:
            shared_data["manual_schedule_draft_series_df_by_key"] = msm.default_manual_series_map()
        _sync_manual_draft_shared_state_locked(shared_data)

    app.layout = build_dashboard_layout(
        config,
        plant_ids,
        plant_name,
        brand_logo_src,
        initial_transport,
        initial_posting_enabled,
        now_tz(config),
    )

    app.clientside_callback(
        """
        function(nClicks, rangeMeta, figure) {
            if (!nClicks || !figure) {
                return window.dash_clientside && window.dash_clientside.no_update
                    ? window.dash_clientside.no_update
                    : null;
            }
            var wrapper = document.getElementById("plots-graph-lib");
            var gd = wrapper ? (wrapper.querySelector(".js-plotly-plot") || wrapper) : null;
            if (!gd || !window.Plotly || !window.Plotly.downloadImage) {
                return "png-lib-unavailable";
            }
            var startToken = (rangeMeta && rangeMeta.start_token) ? rangeMeta.start_token : "start";
            var endToken = (rangeMeta && rangeMeta.end_token) ? rangeMeta.end_token : "end";
            window.Plotly.downloadImage(gd, {
                format: "png",
                filename: "measurements_lib_" + startToken + "_" + endToken
            });
            return "png-lib-" + String(nClicks);
        }
        """,
        Output("plots-lib-png-noop", "children"),
        [Input("plots-download-png-lib-btn", "n_clicks"), Input("plots-range-meta-store", "data")],
        [State("plots-graph-lib", "figure")],
        prevent_initial_call=True,
    )

    app.clientside_callback(
        """
        function(nClicks, rangeMeta, figure) {
            if (!nClicks || !figure) {
                return window.dash_clientside && window.dash_clientside.no_update
                    ? window.dash_clientside.no_update
                    : null;
            }
            var wrapper = document.getElementById("plots-graph-vrfb");
            var gd = wrapper ? (wrapper.querySelector(".js-plotly-plot") || wrapper) : null;
            if (!gd || !window.Plotly || !window.Plotly.downloadImage) {
                return "png-vrfb-unavailable";
            }
            var startToken = (rangeMeta && rangeMeta.start_token) ? rangeMeta.start_token : "start";
            var endToken = (rangeMeta && rangeMeta.end_token) ? rangeMeta.end_token : "end";
            window.Plotly.downloadImage(gd, {
                format: "png",
                filename: "measurements_vrfb_" + startToken + "_" + endToken
            });
            return "png-vrfb-" + String(nClicks);
        }
        """,
        Output("plots-vrfb-png-noop", "children"),
        [Input("plots-download-png-vrfb-btn", "n_clicks"), Input("plots-range-meta-store", "data")],
        [State("plots-graph-vrfb", "figure")],
        prevent_initial_call=True,
    )

    @app.callback(
        [
            Output("transport-mode-selector", "data"),
            Output("transport-local-btn", "className"),
            Output("transport-remote-btn", "className"),
            Output("transport-switch-modal", "className"),
        ],
        [
            Input("transport-local-btn", "n_clicks"),
            Input("transport-remote-btn", "n_clicks"),
            Input("transport-switch-cancel", "n_clicks"),
            Input("transport-switch-confirm", "n_clicks"),
        ],
        [State("transport-mode-selector", "data")],
        prevent_initial_call=False,
    )
    def select_transport_mode(local_clicks, remote_clicks, cancel_clicks, confirm_clicks, current_mode):
        ctx = callback_context
        with shared_data["lock"]:
            stored_mode = shared_data.get("transport_mode", "local")
        hidden_class = "modal-overlay hidden"
        open_class = "modal-overlay"

        if not ctx.triggered:
            if stored_mode == "remote":
                return "remote", "toggle-option", "toggle-option active", hidden_class
            return "local", "toggle-option active", "toggle-option", hidden_class

        trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]

        if trigger_id == "transport-switch-cancel":
            if stored_mode == "remote":
                return "remote", "toggle-option", "toggle-option active", hidden_class
            return "local", "toggle-option active", "toggle-option", hidden_class

        if trigger_id == "transport-switch-confirm":
            intent = transport_switch_intent_from_confirm(trigger_id, stored_mode=stored_mode)
            if intent:
                status = _enqueue_dashboard_control_intent(intent)
                logging.info(
                    "Dashboard: queued transport switch command %s state=%s mode=%s",
                    status.get("id"),
                    status.get("state"),
                    intent.get("requested_mode"),
                )
            requested_mode = (intent or {}).get("requested_mode", ("remote" if stored_mode == "local" else "local"))

            if requested_mode == "remote":
                return "remote", "toggle-option", "toggle-option active", hidden_class
            return "local", "toggle-option active", "toggle-option", hidden_class

        if trigger_id == "transport-remote-btn" and stored_mode != "remote":
            return stored_mode, "toggle-option active", "toggle-option", open_class
        if trigger_id == "transport-local-btn" and stored_mode != "local":
            return stored_mode, "toggle-option", "toggle-option active", open_class

        if stored_mode == "remote":
            return "remote", "toggle-option", "toggle-option active", hidden_class
        return "local", "toggle-option active", "toggle-option", hidden_class

    @app.callback(
        [
            Output("api-posting-toggle-store", "data"),
            Output("api-posting-enable-btn", "className"),
            Output("api-posting-disable-btn", "className"),
            Output("api-posting-enable-btn", "disabled"),
            Output("api-posting-disable-btn", "disabled"),
        ],
        [
            Input("interval-component", "n_intervals"),
            Input("posting-settings-action", "data"),
            Input("api-posting-enable-btn", "n_clicks_timestamp"),
            Input("api-posting-disable-btn", "n_clicks_timestamp"),
        ],
        prevent_initial_call=False,
    )
    def render_api_posting_toggle(_n_intervals, _action_token, enable_click_ts_ms, disable_click_ts_ms):
        def classes_for(enabled):
            if enabled:
                return "toggle-option active", "toggle-option"
            return "toggle-option", "toggle-option active"

        config_default = bool(config.get("ISTENTORE_POST_MEASUREMENTS_IN_API_MODE", True))
        posting_runtime = snapshot_locked(
            shared_data,
            lambda data: dict(data.get("posting_runtime", {}) or {}),
        )
        server_state = str(posting_runtime.get("state") or ("enabled" if posting_runtime.get("policy_enabled") else "disabled"))
        policy_enabled = bool(posting_runtime.get("policy_enabled", config_default))
        feedback_state = resolve_command_click_feedback_state(
            positive_click_ts_ms=enable_click_ts_ms,
            negative_click_ts_ms=disable_click_ts_ms,
            positive_state="enabling",
            negative_state="disabling",
            now_ts=now_tz(config),
            hold_seconds=ui_transition_feedback_hold_s,
        )
        display_state = posting_display_state(server_state, feedback_state)
        controls = posting_controls_state(display_state)
        visual_enabled = display_state in {"enabled", "enabling"}
        enable_class, disable_class = classes_for(visual_enabled)
        return (
            bool(policy_enabled),
            enable_class,
            disable_class,
            bool(controls["enable_disabled"]),
            bool(controls["disable_disabled"]),
        )

    @app.callback(
        Output("posting-settings-action", "data"),
        [Input("api-posting-enable-btn", "n_clicks"), Input("api-posting-disable-btn", "n_clicks")],
        prevent_initial_call=True,
    )
    def enqueue_posting_command(enable_clicks, disable_clicks):
        ctx = callback_context
        if not ctx.triggered:
            raise PreventUpdate
        trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]
        intent = posting_intent_from_trigger(trigger_id)
        if intent is None:
            raise PreventUpdate
        status = _enqueue_dashboard_settings_intent(intent, trigger_id=trigger_id)
        return _command_status_action_token(status)

    @app.callback(
        [
            Output("set-password-btn", "children"),
            Output("set-password-btn", "disabled"),
            Output("disconnect-api-btn", "children"),
            Output("disconnect-api-btn", "disabled"),
        ],
        [
            Input("interval-component", "n_intervals"),
            Input("api-connection-action", "data"),
            Input("set-password-btn", "n_clicks_timestamp"),
            Input("disconnect-api-btn", "n_clicks_timestamp"),
        ],
        prevent_initial_call=False,
    )
    def render_api_connection_buttons(_n, _action_token, connect_click_ts_ms, disconnect_click_ts_ms):
        snapshot = snapshot_locked(
            shared_data,
            lambda data: {
                "api_connection_runtime": dict(data.get("api_connection_runtime", {}) or {}),
            },
        )
        api_runtime = snapshot["api_connection_runtime"]
        feedback_state = resolve_command_click_feedback_state(
            positive_click_ts_ms=connect_click_ts_ms,
            negative_click_ts_ms=disconnect_click_ts_ms,
            positive_state="connecting",
            negative_state="disconnecting",
            now_ts=now_tz(config),
            hold_seconds=ui_transition_feedback_hold_s,
        )
        display_state = api_connection_display_state(api_runtime.get("state"), feedback_state)
        controls = api_connection_controls_state(display_state)
        return (
            controls["connect_label"],
            bool(controls["connect_disabled"]),
            controls["disconnect_label"],
            bool(controls["disconnect_disabled"]),
        )

    @app.callback(
        Output("api-connection-action", "data"),
        [Input("set-password-btn", "n_clicks"), Input("disconnect-api-btn", "n_clicks")],
        [State("api-password", "value")],
        prevent_initial_call=True,
    )
    def enqueue_api_connection_command(connect_clicks, disconnect_clicks, password_value):
        ctx = callback_context
        if not ctx.triggered:
            raise PreventUpdate
        trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]
        intent = api_connection_intent_from_trigger(trigger_id, password_value=password_value)
        if intent is None:
            raise PreventUpdate
        status = _enqueue_dashboard_settings_intent(intent, trigger_id=trigger_id)
        return _command_status_action_token(status)

    @app.callback(
        [
            Output("bulk-control-modal", "className"),
            Output("bulk-control-modal-title", "children"),
            Output("bulk-control-modal-text", "children"),
            Output("bulk-control-request", "data"),
        ],
        [
            Input("start-all-btn", "n_clicks"),
            Input("stop-all-btn", "n_clicks"),
            Input("bulk-control-cancel", "n_clicks"),
            Input("bulk-control-confirm", "n_clicks"),
        ],
        [State("bulk-control-request", "data")],
        prevent_initial_call=False,
    )
    def handle_bulk_control_modal(start_all_clicks, stop_all_clicks, cancel_clicks, confirm_clicks, current_request):
        ctx = callback_context
        hidden_class = "modal-overlay hidden"
        open_class = "modal-overlay"
        default_title = "Confirm Fleet Action"
        default_text = ""

        if not ctx.triggered:
            return hidden_class, default_title, default_text, current_request

        trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]
        if trigger_id == "start-all-btn":
            return (
                open_class,
                "Confirm Start All",
                "Start All will enable recording and start operation for both plants. Continue?",
                "start_all",
            )
        if trigger_id == "stop-all-btn":
            return (
                open_class,
                "Confirm Stop All",
                "Stop All will safe-stop both plants and stop recording for both plants. Continue?",
                "stop_all",
            )
        if trigger_id == "bulk-control-cancel":
            return hidden_class, default_title, default_text, None
        if trigger_id == "bulk-control-confirm":
            return hidden_class, default_title, default_text, current_request

        return hidden_class, default_title, default_text, current_request

    @app.callback(
        Output("control-action", "data"),
        [
            Input("start-lib", "n_clicks"),
            Input("stop-lib", "n_clicks"),
            Input("record-lib", "n_clicks"),
            Input("record-stop-lib", "n_clicks"),
            Input("start-vrfb", "n_clicks"),
            Input("stop-vrfb", "n_clicks"),
            Input("record-vrfb", "n_clicks"),
            Input("record-stop-vrfb", "n_clicks"),
            Input("bulk-control-confirm", "n_clicks"),
        ],
        [State("bulk-control-request", "data")],
        prevent_initial_call=True,
    )
    def handle_controls(*args):
        ctx = callback_context
        if not ctx.triggered:
            raise PreventUpdate

        bulk_request = args[-1]
        trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]
        intent = command_intent_from_control_trigger(trigger_id, bulk_request=bulk_request)
        if intent is None:
            raise PreventUpdate

        status = _enqueue_dashboard_control_intent(intent, trigger_id=trigger_id)
        return _command_status_action_token(status)

    @app.callback(Output("manual-status-text", "children"), Input("manual-editor-status-store", "data"))
    def render_manual_status(status_text):
        return str(status_text or "")

    @app.callback(
        [
            Output("manual-toggle-lib-p-enable-btn", "children"),
            Output("manual-toggle-lib-p-enable-btn", "className"),
            Output("manual-toggle-lib-p-enable-btn", "disabled"),
            Output("manual-toggle-lib-p-disable-btn", "children"),
            Output("manual-toggle-lib-p-disable-btn", "className"),
            Output("manual-toggle-lib-p-disable-btn", "disabled"),
            Output("manual-toggle-lib-p-update-btn", "children"),
            Output("manual-toggle-lib-p-update-btn", "disabled"),
            Output("manual-toggle-lib-q-enable-btn", "children"),
            Output("manual-toggle-lib-q-enable-btn", "className"),
            Output("manual-toggle-lib-q-enable-btn", "disabled"),
            Output("manual-toggle-lib-q-disable-btn", "children"),
            Output("manual-toggle-lib-q-disable-btn", "className"),
            Output("manual-toggle-lib-q-disable-btn", "disabled"),
            Output("manual-toggle-lib-q-update-btn", "children"),
            Output("manual-toggle-lib-q-update-btn", "disabled"),
            Output("manual-toggle-vrfb-p-enable-btn", "children"),
            Output("manual-toggle-vrfb-p-enable-btn", "className"),
            Output("manual-toggle-vrfb-p-enable-btn", "disabled"),
            Output("manual-toggle-vrfb-p-disable-btn", "children"),
            Output("manual-toggle-vrfb-p-disable-btn", "className"),
            Output("manual-toggle-vrfb-p-disable-btn", "disabled"),
            Output("manual-toggle-vrfb-p-update-btn", "children"),
            Output("manual-toggle-vrfb-p-update-btn", "disabled"),
            Output("manual-toggle-vrfb-q-enable-btn", "children"),
            Output("manual-toggle-vrfb-q-enable-btn", "className"),
            Output("manual-toggle-vrfb-q-enable-btn", "disabled"),
            Output("manual-toggle-vrfb-q-disable-btn", "children"),
            Output("manual-toggle-vrfb-q-disable-btn", "className"),
            Output("manual-toggle-vrfb-q-disable-btn", "disabled"),
            Output("manual-toggle-vrfb-q-update-btn", "children"),
            Output("manual-toggle-vrfb-q-update-btn", "disabled"),
        ],
        [
            Input("interval-component", "n_intervals"),
            Input("manual-settings-action", "data"),
            Input("manual-editor-status-store", "data"),
            Input("manual-toggle-lib-p-enable-btn", "n_clicks_timestamp"),
            Input("manual-toggle-lib-p-disable-btn", "n_clicks_timestamp"),
            Input("manual-toggle-lib-p-update-btn", "n_clicks_timestamp"),
            Input("manual-toggle-lib-q-enable-btn", "n_clicks_timestamp"),
            Input("manual-toggle-lib-q-disable-btn", "n_clicks_timestamp"),
            Input("manual-toggle-lib-q-update-btn", "n_clicks_timestamp"),
            Input("manual-toggle-vrfb-p-enable-btn", "n_clicks_timestamp"),
            Input("manual-toggle-vrfb-p-disable-btn", "n_clicks_timestamp"),
            Input("manual-toggle-vrfb-p-update-btn", "n_clicks_timestamp"),
            Input("manual-toggle-vrfb-q-enable-btn", "n_clicks_timestamp"),
            Input("manual-toggle-vrfb-q-disable-btn", "n_clicks_timestamp"),
            Input("manual-toggle-vrfb-q-update-btn", "n_clicks_timestamp"),
        ],
        prevent_initial_call=False,
    )
    def render_manual_series_controls(
        _n_intervals,
        _manual_settings_action,
        _manual_editor_status,
        lib_p_activate_ts,
        lib_p_inactivate_ts,
        lib_p_update_ts,
        lib_q_activate_ts,
        lib_q_inactivate_ts,
        lib_q_update_ts,
        vrfb_p_activate_ts,
        vrfb_p_inactivate_ts,
        vrfb_p_update_ts,
        vrfb_q_activate_ts,
        vrfb_q_inactivate_ts,
        vrfb_q_update_ts,
    ):
        ts_map = {
            "lib_p": {"activate": lib_p_activate_ts, "inactivate": lib_p_inactivate_ts, "update": lib_p_update_ts},
            "lib_q": {"activate": lib_q_activate_ts, "inactivate": lib_q_inactivate_ts, "update": lib_q_update_ts},
            "vrfb_p": {"activate": vrfb_p_activate_ts, "inactivate": vrfb_p_inactivate_ts, "update": vrfb_p_update_ts},
            "vrfb_q": {"activate": vrfb_q_activate_ts, "inactivate": vrfb_q_inactivate_ts, "update": vrfb_q_update_ts},
        }
        snapshot = _get_manual_series_snapshot()
        draft_series_map = snapshot["draft_series_map"]
        applied_series_map = snapshot["applied_series_map"]
        runtime_state_map = snapshot["runtime_state"]
        now_value = now_tz(config)

        outputs = []
        for key in ("lib_p", "lib_q", "vrfb_p", "vrfb_q"):
            runtime = dict(runtime_state_map.get(key, {}) or {})
            server_state = str(runtime.get("state") or ("active" if runtime.get("active") else "inactive"))
            click_ts = ts_map.get(key, {})
            display_state = manual_series_display_state(
                server_state,
                resolve_command_click_feedback_state(
                    positive_click_ts_ms=click_ts.get("activate"),
                    negative_click_ts_ms=click_ts.get("inactivate"),
                    positive_state="activating",
                    negative_state="inactivating",
                    now_ts=now_value,
                    hold_seconds=ui_transition_feedback_hold_s,
                )
                or (
                    "updating"
                    if resolve_command_click_feedback_state(
                        positive_click_ts_ms=click_ts.get("update"),
                        negative_click_ts_ms=None,
                        positive_state="updating",
                        negative_state="updating",
                        now_ts=now_value,
                        hold_seconds=ui_transition_feedback_hold_s,
                    )
                    == "updating"
                    else None
                ),
            )
            draft_df = draft_series_map.get(key, pd.DataFrame())
            applied_df = applied_series_map.get(key, pd.DataFrame())
            has_draft_rows = not msm.normalize_manual_series_df(draft_df, timezone_name=config.get("TIMEZONE_NAME")).empty
            is_dirty = _manual_series_is_dirty(key, draft_df, applied_df)
            control_state = manual_series_controls_state(display_state, has_draft_rows=has_draft_rows, is_dirty=is_dirty)
            active_cls, inactive_cls = _manual_toggle_classes(bool(control_state["active_visual"]))
            outputs.extend(
                [
                    control_state["activate_label"],
                    active_cls,
                    bool(control_state["activate_disabled"]),
                    control_state["inactivate_label"],
                    inactive_cls,
                    bool(control_state["inactivate_disabled"]),
                    control_state["update_label"],
                    bool(control_state["update_disabled"]),
                ]
            )
        return tuple(outputs)

    @app.callback(
        Output("manual-settings-action", "data"),
        [
            Input("manual-toggle-lib-p-enable-btn", "n_clicks"),
            Input("manual-toggle-lib-p-disable-btn", "n_clicks"),
            Input("manual-toggle-lib-p-update-btn", "n_clicks"),
            Input("manual-toggle-lib-q-enable-btn", "n_clicks"),
            Input("manual-toggle-lib-q-disable-btn", "n_clicks"),
            Input("manual-toggle-lib-q-update-btn", "n_clicks"),
            Input("manual-toggle-vrfb-p-enable-btn", "n_clicks"),
            Input("manual-toggle-vrfb-p-disable-btn", "n_clicks"),
            Input("manual-toggle-vrfb-p-update-btn", "n_clicks"),
            Input("manual-toggle-vrfb-q-enable-btn", "n_clicks"),
            Input("manual-toggle-vrfb-q-disable-btn", "n_clicks"),
            Input("manual-toggle-vrfb-q-update-btn", "n_clicks"),
        ],
        prevent_initial_call=True,
    )
    def enqueue_manual_series_commands(*_clicks):
        ctx = callback_context
        if not ctx.triggered:
            raise PreventUpdate
        trigger_id = _parse_trigger_id(ctx.triggered[0]["prop_id"])
        draft_map = snapshot_locked(shared_data, lambda data: dict(data.get("manual_schedule_draft_series_df_by_key", {})))
        intent = manual_settings_intent_from_trigger(trigger_id, draft_series_by_key=draft_map, tz=tz)
        if intent is None:
            raise PreventUpdate
        status = _enqueue_dashboard_settings_intent(intent, trigger_id=trigger_id, log_label="manual settings command")
        return _command_status_action_token(status)

    @app.callback(
        [
            Output("manual-graph-lib-p", "figure"),
            Output("manual-graph-lib-q", "figure"),
            Output("manual-graph-vrfb-p", "figure"),
            Output("manual-graph-vrfb-q", "figure"),
        ],
        [
            Input("interval-component", "n_intervals"),
            Input("manual-editor-rows-store", "data"),
            Input("manual-editor-status-store", "data"),
            Input("manual-settings-action", "data"),
        ],
        prevent_initial_call=False,
    )
    def update_manual_override_plots(*_):
        snapshot = _get_manual_series_snapshot()
        draft_series_map = snapshot["draft_series_map"]
        applied_series_map = snapshot["applied_series_map"]
        runtime_state = snapshot["runtime_state"]
        window_start, window_end = _manual_window_bounds()

        def fig_for(series_key):
            meta = msm.MANUAL_SERIES_META[series_key]
            is_p = meta["signal"] == "p"
            return create_manual_series_figure(
                title=f"{meta['label']} (Manual Override)",
                unit_label=meta["unit"],
                staged_series_df=draft_series_map.get(series_key, pd.DataFrame()),
                applied_series_df=applied_series_map.get(series_key, pd.DataFrame()),
                applied_enabled=bool(dict(runtime_state.get(series_key, {}) or {}).get("active", False)),
                tz=tz,
                plot_theme=plot_theme,
                line_color=trace_colors["p_setpoint"] if is_p else trace_colors["q_setpoint"],
                x_window_start=window_start,
                x_window_end=window_end,
                uirevision_key=f"manual-{series_key}",
            )

        return (
            fig_for("lib_p"),
            fig_for("lib_q"),
            fig_for("vrfb_p"),
            fig_for("vrfb_q"),
        )

    @app.callback(
        [
            Output("manual-editor-rows-store", "data"),
            Output("manual-editor-start-date", "date"),
            Output("manual-editor-start-hour", "value"),
            Output("manual-editor-start-minute", "value"),
            Output("manual-editor-start-second", "value"),
        ],
        Input("manual-editor-series-selector", "value"),
        prevent_initial_call=False,
    )
    def load_manual_editor_for_selected_series(series_key):
        if series_key not in msm.MANUAL_SERIES_KEYS:
            series_key = "lib_p"
        with shared_data["lock"]:
            _sync_manual_draft_shared_state_locked(shared_data)
            series_df = dict(shared_data.get("manual_schedule_draft_series_df_by_key", {})).get(series_key, pd.DataFrame())
        start_ts, rows = msm.manual_series_df_to_editor_rows_and_start(series_df, timezone_name=config.get("TIMEZONE_NAME"))
        if start_ts is None or pd.isna(start_ts):
            start_ts = normalize_timestamp_value(now_tz(config), tz)
        return (
            rows,
            start_ts.date(),
            int(start_ts.hour),
            int(start_ts.minute),
            int(start_ts.second),
        )

    @app.callback(
        Output("manual-editor-clear-confirm", "displayed"),
        Input("manual-editor-clear-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def prompt_manual_clear(_n_clicks):
        return True

    @app.callback(
        [
            Output("manual-editor-delete-confirm", "displayed"),
            Output("manual-editor-delete-index-store", "data"),
        ],
        Input({"type": "manual-row-del", "index": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def prompt_manual_delete(_delete_clicks):
        ctx = callback_context
        if not ctx.triggered:
            raise PreventUpdate
        triggered_value = ctx.triggered[0].get("value")
        if not isinstance(triggered_value, (int, float)) or int(triggered_value) <= 0:
            raise PreventUpdate
        trigger_id = _parse_trigger_id(ctx.triggered[0]["prop_id"])
        if not isinstance(trigger_id, dict):
            raise PreventUpdate
        return True, int(trigger_id.get("index", -1))

    @app.callback(
        [
            Output("manual-editor-rows-store", "data", allow_duplicate=True),
            Output("manual-editor-delete-index-store", "data", allow_duplicate=True),
            Output("manual-editor-status-store", "data", allow_duplicate=True),
        ],
        [
            Input("manual-editor-add-first-row-btn", "n_clicks"),
            Input({"type": "manual-row-add", "index": ALL}, "n_clicks"),
            Input({"type": "manual-row-hours", "index": ALL}, "value"),
            Input({"type": "manual-row-minutes", "index": ALL}, "value"),
            Input({"type": "manual-row-seconds", "index": ALL}, "value"),
            Input({"type": "manual-row-setpoint", "index": ALL}, "value"),
            Input("manual-editor-clear-confirm", "submit_n_clicks"),
            Input("manual-editor-delete-confirm", "submit_n_clicks"),
            Input("manual-editor-csv-upload", "contents"),
            Input("manual-editor-csv-upload", "last_modified"),
        ],
        [
            State("manual-editor-rows-store", "data"),
            State("manual-editor-delete-index-store", "data"),
            State("manual-editor-csv-upload", "contents"),
            State("manual-editor-csv-upload", "filename"),
        ],
        prevent_initial_call=True,
    )
    def mutate_manual_editor_rows(
        add_first_clicks,
        row_add_clicks,
        row_hours,
        row_minutes,
        row_seconds,
        row_setpoints,
        clear_submit,
        delete_submit,
        upload_contents,
        upload_last_modified,
        current_rows,
        pending_delete_index,
        upload_contents_state,
        upload_filename_state,
    ):
        ctx = callback_context
        if not ctx.triggered:
            raise PreventUpdate

        current_rows = list(current_rows or [])
        raw_trigger_prop = str(ctx.triggered[0]["prop_id"])
        trigger_id = _parse_trigger_id(raw_trigger_prop)

        def _normalize_rows_list(rows):
            normalized = []
            for idx, row in enumerate(list(rows or [])):
                item = dict(row or {})
                item["hours"] = int(item.get("hours", 0) or 0)
                item["minutes"] = int(item.get("minutes", 0) or 0)
                item["seconds"] = int(item.get("seconds", 0) or 0)
                try:
                    item["setpoint"] = float(item.get("setpoint", 0.0) or 0.0)
                except (TypeError, ValueError):
                    item["setpoint"] = 0.0
                if idx == 0:
                    item["hours"] = 0
                    item["minutes"] = 0
                    item["seconds"] = 0
                normalized.append(item)
            return normalized

        rows = _normalize_rows_list(current_rows)

        if trigger_id == "manual-editor-csv-upload":
            if raw_trigger_prop.endswith(".last_modified"):
                # Wait for the actual contents event; processing on last_modified can read stale contents.
                raise PreventUpdate
            effective_upload_contents = upload_contents if upload_contents is not None else upload_contents_state
            if not effective_upload_contents:
                raise PreventUpdate
            try:
                _, content_string = effective_upload_contents.split(",", 1)
                csv_text = base64.b64decode(content_string).decode("utf-8")
                loaded_rows = msm.load_manual_editor_rows_from_relative_csv_text(csv_text)
                display_name = upload_filename_state or "uploaded file"
                return loaded_rows, None, f"Loaded CSV '{display_name}' into editor."
            except Exception as exc:
                return dash.no_update, dash.no_update, f"CSV load failed: {exc}"

        if trigger_id == "manual-editor-clear-confirm":
            return [], None, "Cleared selected manual schedule editor rows."

        if trigger_id == "manual-editor-delete-confirm":
            try:
                delete_idx = int(pending_delete_index)
            except (TypeError, ValueError):
                return dash.no_update, None, "Delete request ignored: no row selected."
            if delete_idx < 0 or delete_idx >= len(rows):
                return dash.no_update, None, "Delete request ignored: invalid row."
            if delete_idx == 0 and len(rows) == 1:
                return dash.no_update, None, "Delete request ignored: first row cannot be removed when it is the only row."
            rows.pop(delete_idx)
            if rows:
                rows[0]["hours"] = 0
                rows[0]["minutes"] = 0
                rows[0]["seconds"] = 0
            return rows, None, f"Deleted breakpoint row {delete_idx + 1}."

        if trigger_id == "manual-editor-add-first-row-btn":
            if not rows:
                return [{"hours": 0, "minutes": 0, "seconds": 0, "setpoint": 0.0}], None, dash.no_update
            last = dict(rows[-1])
            rows.append(last)
            return rows, None, dash.no_update

        if isinstance(trigger_id, dict) and trigger_id.get("type") == "manual-row-add":
            try:
                idx = int(trigger_id.get("index", -1))
            except (TypeError, ValueError):
                idx = -1
            if idx < 0 or idx >= len(rows):
                raise PreventUpdate
            new_row = dict(rows[idx])
            rows.insert(idx + 1, new_row)
            if rows:
                rows[0]["hours"] = 0
                rows[0]["minutes"] = 0
                rows[0]["seconds"] = 0
            return rows, None, dash.no_update

        if isinstance(trigger_id, dict) and str(trigger_id.get("type", "")).startswith("manual-row-"):
            row_count = max(
                len(row_hours or []),
                len(row_minutes or []),
                len(row_seconds or []),
                len(row_setpoints or []),
                len(rows),
            )
            new_rows = []
            for idx in range(row_count):
                prev = rows[idx] if idx < len(rows) else {}
                new_rows.append(
                    {
                        "hours": 0 if idx == 0 else int((row_hours or [])[idx] if idx < len(row_hours or []) and (row_hours or [])[idx] is not None else prev.get("hours", 0) or 0),
                        "minutes": 0 if idx == 0 else int((row_minutes or [])[idx] if idx < len(row_minutes or []) and (row_minutes or [])[idx] is not None else prev.get("minutes", 0) or 0),
                        "seconds": 0 if idx == 0 else int((row_seconds or [])[idx] if idx < len(row_seconds or []) and (row_seconds or [])[idx] is not None else prev.get("seconds", 0) or 0),
                        "setpoint": (
                            float((row_setpoints or [])[idx])
                            if idx < len(row_setpoints or []) and (row_setpoints or [])[idx] is not None
                            else float(prev.get("setpoint", 0.0) or 0.0)
                        ),
                    }
                )
            if new_rows:
                new_rows[0]["hours"] = 0
                new_rows[0]["minutes"] = 0
                new_rows[0]["seconds"] = 0
            return new_rows, dash.no_update, dash.no_update

        raise PreventUpdate

    @app.callback(
        Output("manual-breakpoint-rows", "children"),
        Input("manual-editor-rows-store", "data"),
    )
    def render_manual_breakpoint_rows(rows):
        rows = list(rows or [])
        if not rows:
            return []

        compact_time_style = {"width": "44px", "minWidth": "44px", "padding": "2px 3px", "height": "28px"}
        compact_setpoint_style = {"width": "86px", "minWidth": "86px", "padding": "2px 6px", "height": "28px"}
        row_action_btn_style = {
            "padding": "3px 0",
            "width": "28px",
            "minWidth": "28px",
            "height": "28px",
            "fontSize": "1.05rem",
            "lineHeight": "1",
            "fontWeight": "800",
        }
        row_style = {
            "display": "flex",
            "alignItems": "center",
            "gap": "2px",
            "flexWrap": "nowrap",
            "overflowX": "auto",
            "padding": "4px 0",
        }
        time_group_style = {
            "display": "flex",
            "alignItems": "center",
            "gap": "6px",
            "flexWrap": "nowrap",
            "minWidth": "174px",
        }
        action_group_style = {"display": "flex", "gap": "4px", "flexWrap": "nowrap", "marginLeft": "4px"}

        header = html.Div(
            style=row_style,
            children=[
                html.Div("Time (HH MM SS)", style={"minWidth": "174px", "fontWeight": "600"}),
                html.Div("Setpoint", style={"minWidth": "86px", "fontWeight": "600"}),
                html.Div("Actions", style={"minWidth": "60px", "fontWeight": "600"}),
            ],
        )
        row_children = [header]
        for idx, row in enumerate(rows):
            row_children.append(
                html.Div(
                    style=row_style,
                    children=[
                        html.Div(
                            style=time_group_style,
                            children=[
                                dcc.Input(
                                    id={"type": "manual-row-hours", "index": idx},
                                    type="number",
                                    className="form-control",
                                    min=0,
                                    step=1,
                                    value=int(row.get("hours", 0) or 0),
                                    disabled=(idx == 0),
                                    style=compact_time_style,
                                ),
                                dcc.Input(
                                    id={"type": "manual-row-minutes", "index": idx},
                                    type="number",
                                    className="form-control",
                                    min=0,
                                    max=59,
                                    step=1,
                                    value=int(row.get("minutes", 0) or 0),
                                    disabled=(idx == 0),
                                    style=compact_time_style,
                                ),
                                dcc.Input(
                                    id={"type": "manual-row-seconds", "index": idx},
                                    type="number",
                                    className="form-control",
                                    min=0,
                                    max=59,
                                    step=1,
                                    value=int(row.get("seconds", 0) or 0),
                                    disabled=(idx == 0),
                                    style=compact_time_style,
                                ),
                            ],
                        ),
                        dcc.Input(
                            id={"type": "manual-row-setpoint", "index": idx},
                            type="number",
                            className="form-control",
                            step="any",
                            value=row.get("setpoint", 0.0),
                            style=compact_setpoint_style,
                        ),
                        html.Div(
                            style=action_group_style,
                            children=[
                                html.Button(
                                    "+",
                                    id={"type": "manual-row-add", "index": idx},
                                    className="btn btn-primary",
                                    n_clicks=0,
                                    title="Add row after",
                                    style=row_action_btn_style,
                                ),
                                html.Button(
                                    "-",
                                    id={"type": "manual-row-del", "index": idx},
                                    className="btn btn-danger",
                                    n_clicks=0,
                                    title="Delete row",
                                    style=row_action_btn_style,
                                ),
                            ],
                        ),
                    ],
                )
            )
        return row_children

    @app.callback(
        [
            Output("manual-editor-add-row-container", "style"),
            Output("manual-breakpoint-rows-container", "style"),
        ],
        Input("manual-editor-rows-store", "data"),
        prevent_initial_call=False,
    )
    def toggle_manual_editor_add_or_list(rows):
        has_rows = bool(list(rows or []))
        if has_rows:
            return {"display": "none"}, {"display": "block"}
        return {"display": "block"}, {"display": "none"}

    @app.callback(
        Output("manual-editor-download", "data"),
        Input("manual-editor-save-csv-btn", "n_clicks"),
        [State("manual-editor-rows-store", "data"), State("manual-editor-series-selector", "value")],
        prevent_initial_call=True,
    )
    def download_manual_editor_csv(n_clicks, rows, series_key):
        if not n_clicks:
            raise PreventUpdate
        csv_text = msm.manual_editor_rows_to_relative_csv_text(rows or [])
        filename = f"manual_{series_key or 'schedule'}.csv"
        return dcc.send_string(csv_text, filename)

    @app.callback(
        Output("manual-editor-status-store", "data"),
        [
            Input("manual-editor-rows-store", "data"),
            Input("manual-editor-start-date", "date"),
            Input("manual-editor-start-hour", "value"),
            Input("manual-editor-start-minute", "value"),
            Input("manual-editor-start-second", "value"),
        ],
        State("manual-editor-series-selector", "value"),
        prevent_initial_call=True,
    )
    def persist_manual_editor_to_shared(rows, start_date, start_hour, start_minute, start_second, series_key):
        try:
            start_dt = None
            if list(rows or []):
                start_dt = _editor_start_to_datetime(start_date, start_hour, start_minute, start_second)
            _set_manual_series_from_editor(series_key, rows or [], start_dt)
            count = len(list(rows or []))
            meta = msm.MANUAL_SERIES_META.get(series_key or "", {})
            label = meta.get("label", str(series_key or "schedule"))
            return f"{label}: staged {count} breakpoint(s)."
        except Exception as exc:
            return f"Manual editor validation failed: {exc}"

    @app.callback(
        [
            Output("api-connection-status", "children"),
            Output("api-measurement-posting-status", "children"),
            Output("api-preview-graph", "figure"),
        ],
        [
            Input("interval-component", "n_intervals"),
            Input("api-connection-action", "data"),
            Input("posting-settings-action", "data"),
        ],
    )
    def update_api_tab(n_intervals, api_connection_action, posting_settings_action):
        with shared_data["lock"]:
            status = shared_data.get("data_fetcher_status", {}).copy()
            api_password = shared_data.get("api_password")
            api_connection_runtime = dict(shared_data.get("api_connection_runtime", {}) or {})
            posting_runtime = dict(shared_data.get("posting_runtime", {}) or {})
            api_map = {
                plant_id: shared_data.get("api_schedule_df_by_plant", {}).get(plant_id, pd.DataFrame()).copy()
                for plant_id in plant_ids
            }
            post_status_map = {
                plant_id: dict((shared_data.get("measurement_post_status", {}) or {}).get(plant_id, {}) or {})
                for plant_id in plant_ids
            }
        auth_state = "Password stored" if api_password else "No stored password"
        posting_policy_enabled = bool(
            posting_runtime.get(
                "policy_enabled",
                config.get("ISTENTORE_POST_MEASUREMENTS_IN_API_MODE", True),
            )
        )
        api_state_display = api_connection_display_state(api_connection_runtime.get("state"), None)
        posting_policy_state = posting_display_state(posting_runtime.get("state"), None)
        api_intended_connected = str(api_connection_runtime.get("desired_state", "disconnected")) == "connected"
        posting_effective = bool(posting_policy_enabled and api_intended_connected and bool(api_password))
        posting_effective_text = "Active" if posting_effective else "Blocked"

        points_today = status.get("today_points_by_plant", {})
        points_tomorrow = status.get("tomorrow_points_by_plant", {})
        status_text = (
            f"API Connection: {api_state_display.capitalize()} | {auth_state} | "
            f"Posting Policy: {posting_policy_state.capitalize()} | "
            f"Posting Effective: {posting_effective_text} | "
            f"Today {status.get('today_date')}: LIB={points_today.get('lib', 0)} VRFB={points_today.get('vrfb', 0)} | "
            f"Tomorrow {status.get('tomorrow_date')}: LIB={points_tomorrow.get('lib', 0)} VRFB={points_tomorrow.get('vrfb', 0)}"
        )
        api_error_text = None
        runtime_error = api_connection_runtime.get("last_error")
        if isinstance(runtime_error, dict):
            api_error_text = runtime_error.get("message")
        if api_error_text:
            status_text += f" | Error: {api_error_text}"

        def format_ts(value):
            ts = normalize_timestamp_value(value, tz)
            if pd.isna(ts):
                return None
            return ts.strftime("%Y-%m-%d %H:%M:%S %Z")

        def format_value(value):
            try:
                return f"{float(value):.3f}"
            except (TypeError, ValueError):
                return "n/a"

        def metric_label(metric):
            mapping = {"soc": "SoC", "p": "P", "q": "Q", "v": "V"}
            return mapping.get(str(metric).lower(), str(metric).upper())

        def build_plant_posting_card(plant_id):
            plant_status = post_status_map.get(plant_id, {})
            posting_enabled = bool(plant_status.get("posting_enabled", False))

            last_success = plant_status.get("last_success") if isinstance(plant_status.get("last_success"), dict) else None
            if last_success:
                success_ts = format_ts(last_success.get("timestamp")) or "n/a"
                success_meas_ts = format_ts(last_success.get("measurement_timestamp")) or "n/a"
                success_text = (
                    f"Metric={metric_label(last_success.get('metric'))} "
                    f"Series={last_success.get('series_id')} "
                    f"Value={format_value(last_success.get('value'))} | "
                    f"Measurement ts: {success_meas_ts} | Sent: {success_ts}"
                )
            else:
                success_text = "No successful post yet."

            last_attempt = plant_status.get("last_attempt") if isinstance(plant_status.get("last_attempt"), dict) else None
            if last_attempt:
                attempt_ts = format_ts(last_attempt.get("timestamp")) or "n/a"
                attempt_result = str(last_attempt.get("result") or "unknown").upper()
                attempt_text = (
                    f"Metric={metric_label(last_attempt.get('metric'))} "
                    f"Series={last_attempt.get('series_id')} "
                    f"Value={format_value(last_attempt.get('value'))} "
                    f"Attempt={last_attempt.get('attempt')} "
                    f"Result={attempt_result} | At: {attempt_ts}"
                )
                next_retry_s = last_attempt.get("next_retry_seconds")
                if next_retry_s is not None and attempt_result == "FAILED":
                    attempt_text += f" | Next retry in ~{next_retry_s}s"
            else:
                attempt_text = "No attempts yet."

            last_error = plant_status.get("last_error") if isinstance(plant_status.get("last_error"), dict) else None
            if last_error:
                error_ts = format_ts(last_error.get("timestamp")) or "n/a"
                error_text = f"{error_ts}: {last_error.get('message')}"
            else:
                error_text = "No errors."

            pending_count = int(plant_status.get("pending_queue_count", 0) or 0)
            oldest_age_s = plant_status.get("oldest_pending_age_s")
            oldest_age_text = "n/a" if oldest_age_s is None else f"{oldest_age_s}s"
            last_enqueue_text = format_ts(plant_status.get("last_enqueue")) or "n/a"

            return html.Div(
                className="posting-card",
                children=[
                    html.H4(f"{plant_name(plant_id)}", className="posting-card-title"),
                    html.Div(f"Posting enabled: {posting_enabled}", className="status-text"),
                    html.Div(f"Pending queue: {pending_count} | Oldest pending age: {oldest_age_text}", className="status-text"),
                    html.Div(f"Last enqueue: {last_enqueue_text}", className="status-text"),
                    html.Div(f"Last successful post: {success_text}", className="status-text"),
                    html.Div(f"Last attempt: {attempt_text}", className="status-text"),
                    html.Div(f"Last error: {error_text}", className="status-text"),
                ],
            )

        posting_cards = html.Div(
            className="posting-section",
            children=[
                html.H4("Measurement Posting", className="posting-section-title"),
                html.Div(
                    className="posting-grid",
                    children=[build_plant_posting_card(plant_id) for plant_id in plant_ids],
                ),
            ],
        )

        fig = go.Figure()
        colors = {"lib": trace_colors["api_lib"], "vrfb": trace_colors["api_vrfb"]}
        for plant_id in plant_ids:
            df = normalize_schedule_index(api_map.get(plant_id, pd.DataFrame()), tz)
            if df.empty:
                continue
            fig.add_trace(
                go.Scatter(
                    x=df.index,
                    y=df.get("power_setpoint_kw", []),
                    mode="lines",
                    line_shape="hv",
                    name=f"{plant_name(plant_id)} API P Setpoint",
                    line=dict(color=colors.get(plant_id, plot_theme["muted"]), width=2),
                )
            )

        if not fig.data:
            fig.add_annotation(text="No API schedule available.", xref="paper", yref="paper", x=0.5, y=0.5, showarrow=False)

        apply_figure_theme(
            fig,
            plot_theme,
            height=340,
            margin=dict(l=40, r=20, t=40, b=30),
            uirevision="api-preview",
        )
        fig.update_yaxes(title_text="kW")
        return status_text, posting_cards, fig

    @app.callback(
        [
            Output("api-status-inline", "children"),
            Output("control-engine-status-inline", "children"),
            Output("control-queue-status-inline", "children"),
            Output("status-lib", "children"),
            Output("status-vrfb", "children"),
            Output("graph-lib", "figure"),
            Output("graph-vrfb", "figure"),
            Output("start-lib", "children"),
            Output("start-lib", "disabled"),
            Output("stop-lib", "children"),
            Output("stop-lib", "disabled"),
            Output("record-lib", "children"),
            Output("record-lib", "disabled"),
            Output("record-stop-lib", "children"),
            Output("record-stop-lib", "disabled"),
            Output("start-vrfb", "children"),
            Output("start-vrfb", "disabled"),
            Output("stop-vrfb", "children"),
            Output("stop-vrfb", "disabled"),
            Output("record-vrfb", "children"),
            Output("record-vrfb", "disabled"),
            Output("record-stop-vrfb", "children"),
            Output("record-stop-vrfb", "disabled"),
        ],
        [
            Input("interval-component", "n_intervals"),
            Input("control-action", "data"),
            Input("start-lib", "n_clicks_timestamp"),
            Input("stop-lib", "n_clicks_timestamp"),
            Input("start-vrfb", "n_clicks_timestamp"),
            Input("stop-vrfb", "n_clicks_timestamp"),
        ],
    )
    def update_status_and_graphs(
        n_intervals,
        control_action,
        start_lib_click_ts_ms,
        stop_lib_click_ts_ms,
        start_vrfb_click_ts_ms,
        stop_vrfb_click_ts_ms,
    ):
        with shared_data["lock"]:
            transport_mode = shared_data.get("transport_mode", "local")
            scheduler_running = dict(shared_data.get("scheduler_running_by_plant", {}))
            transition_by_plant = dict(shared_data.get("plant_transition_by_plant", {}))
            recording_files = dict(shared_data.get("measurements_filename_by_plant", {}))
            observed_state_by_plant = dict(shared_data.get("plant_observed_state_by_plant", {}))
            control_engine_status = dict(shared_data.get("control_engine_status", {}))
            status = shared_data.get("data_fetcher_status", {}).copy()
            api_schedule_map = {
                plant_id: shared_data.get("api_schedule_df_by_plant", {}).get(plant_id, pd.DataFrame()).copy()
                for plant_id in plant_ids
            }
            manual_series_map = dict(shared_data.get("manual_schedule_series_df_by_key", {}))
            manual_merge_enabled = dict(shared_data.get("manual_schedule_merge_enabled_by_key", {}))
            measurements_map = {
                plant_id: shared_data.get("current_file_df_by_plant", {}).get(plant_id, pd.DataFrame()).copy()
                for plant_id in plant_ids
            }

        status_now = now_tz(config)
        enable_state_by_plant = {}
        for plant_id in plant_ids:
            observed = dict(observed_state_by_plant.get(plant_id, {}) or {})
            if bool(observed.get("stale", True)):
                enable_state_by_plant[plant_id] = None
            else:
                enable_state_by_plant[plant_id] = observed.get("enable_state")
        _engine_state_by_plant = {
            plant_id: resolve_runtime_transition(
                plant_id,
                transition_by_plant.get(plant_id, "unknown"),
                enable_state_by_plant.get(plant_id),
            )
            for plant_id in plant_ids
        }
        click_feedback_by_plant = {
            "lib": resolve_click_feedback_transition_state(
                start_click_ts_ms=start_lib_click_ts_ms,
                stop_click_ts_ms=stop_lib_click_ts_ms,
                now_ts=status_now,
                hold_seconds=ui_transition_feedback_hold_s,
            ),
            "vrfb": resolve_click_feedback_transition_state(
                start_click_ts_ms=start_vrfb_click_ts_ms,
                stop_click_ts_ms=stop_vrfb_click_ts_ms,
                now_ts=status_now,
                hold_seconds=ui_transition_feedback_hold_s,
            ),
        }
        runtime_state_by_plant = {}
        for plant_id in plant_ids:
            pending_feedback_state = click_feedback_by_plant.get(plant_id)
            if pending_feedback_state in {"starting", "stopping"}:
                runtime_state_by_plant[plant_id] = pending_feedback_state
                continue
            runtime_state_by_plant[plant_id] = _engine_state_by_plant.get(plant_id, "unknown")

        api_inline = (
            f"API Connected: {bool(status.get('connected'))} | "
            f"Today {status.get('today_date')}: LIB={status.get('today_points_by_plant', {}).get('lib', 0)} "
            f"VRFB={status.get('today_points_by_plant', {}).get('vrfb', 0)} | "
            f"Tomorrow {status.get('tomorrow_date')}: LIB={status.get('tomorrow_points_by_plant', {}).get('lib', 0)} "
            f"VRFB={status.get('tomorrow_points_by_plant', {}).get('vrfb', 0)}"
        )
        if status.get("error"):
            api_inline += f" | Error: {status.get('error')}"
        control_engine_inline = summarize_control_engine_status(control_engine_status, status_now)
        control_queue_inline = summarize_control_queue_status(control_engine_status)

        status_window_start = status_now.replace(hour=0, minute=0, second=0, microsecond=0)
        status_window_end = status_window_start + timedelta(days=2)

        def plant_status_text(plant_id):
            recording = recording_files.get(plant_id)
            runtime_state = runtime_state_by_plant.get(plant_id, "unknown")
            rec_text = f"Recording: On ({os.path.basename(recording)})" if recording else "Recording: Off"
            observed = dict(observed_state_by_plant.get(plant_id, {}) or {})
            health_lines = summarize_plant_modbus_health(observed, status_now)
            rows = [
                html.Div(
                    (
                        f"{plant_name(plant_id)} | Plant State: {runtime_state.capitalize()} | {rec_text}"
                    ),
                    className="status-text",
                )
            ]
            rows.extend(html.Div(text, className="status-text") for text in health_lines)
            return rows

        effective_schedule_map = {}
        for plant_id in plant_ids:
            p_key, q_key = msm.manual_series_keys_for_plant(plant_id)
            effective_schedule_map[plant_id] = build_effective_schedule_frame(
                api_schedule_map.get(plant_id, pd.DataFrame()),
                manual_series_map.get(p_key, pd.DataFrame()),
                manual_series_map.get(q_key, pd.DataFrame()),
                manual_p_enabled=bool(manual_merge_enabled.get(p_key, False)),
                manual_q_enabled=bool(manual_merge_enabled.get(q_key, False)),
                tz=tz,
            )

        lib_schedule = normalize_schedule_index(effective_schedule_map.get("lib", pd.DataFrame()), tz)
        vrfb_schedule = normalize_schedule_index(effective_schedule_map.get("vrfb", pd.DataFrame()), tz)
        lib_fig = create_plant_figure(
            "lib",
            plant_name,
            lib_schedule,
            measurements_map.get("lib", pd.DataFrame()),
            uirevision_key=f"lib:merged:{transport_mode}",
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
            measurements_map.get("vrfb", pd.DataFrame()),
            uirevision_key=f"vrfb:merged:{transport_mode}",
            tz=tz,
            plot_theme=plot_theme,
            trace_colors=trace_colors,
            x_window_start=status_window_start,
            x_window_end=status_window_end,
            time_indicator_ts=status_now,
            voltage_autorange_padding_kv=_voltage_padding_kv_for_plant("vrfb"),
        )

        lib_controls = get_plant_control_labels_and_disabled(
            runtime_state_by_plant.get("lib", "unknown"),
            bool(recording_files.get("lib")),
        )
        vrfb_controls = get_plant_control_labels_and_disabled(
            runtime_state_by_plant.get("vrfb", "unknown"),
            bool(recording_files.get("vrfb")),
        )

        return (
            api_inline,
            control_engine_inline,
            control_queue_inline,
            plant_status_text("lib"),
            plant_status_text("vrfb"),
            lib_fig,
            vrfb_fig,
            lib_controls[0],
            lib_controls[1],
            lib_controls[2],
            lib_controls[3],
            lib_controls[4],
            lib_controls[5],
            lib_controls[6],
            lib_controls[7],
            vrfb_controls[0],
            vrfb_controls[1],
            vrfb_controls[2],
            vrfb_controls[3],
            vrfb_controls[4],
            vrfb_controls[5],
            vrfb_controls[6],
            vrfb_controls[7],
        )

    @app.callback(
        [
            Output("plots-index-store", "data"),
            Output("plots-range-slider", "min"),
            Output("plots-range-slider", "max"),
            Output("plots-range-slider", "value"),
            Output("plots-range-slider", "marks"),
            Output("plots-range-slider", "disabled"),
            Output("plots-status-text", "children"),
        ],
        [Input("main-tabs", "value"), Input("plots-refresh-interval", "n_intervals")],
        [State("plots-range-slider", "value")],
        prevent_initial_call=False,
    )
    def update_historical_plots_index(active_tab, plots_refresh_n, current_slider_value):
        if active_tab != "plots":
            raise PreventUpdate

        plant_suffix_by_id = {plant_id: sanitize_plant_name(plant_name(plant_id), plant_id) for plant_id in plant_ids}
        index_data = scan_measurement_history_index("data", plant_suffix_by_id, tz)

        if not index_data.get("has_data"):
            return (
                index_data,
                0,
                1,
                [0, 1],
                {},
                True,
                "No measurement files found in data/.",
            )

        global_start_ms = int(index_data["global_start_ms"])
        global_end_ms = int(index_data["global_end_ms"])
        selected_range = clamp_epoch_range(current_slider_value, global_start_ms, global_end_ms)

        slider_min = global_start_ms
        slider_max = global_end_ms if global_end_ms > global_start_ms else global_start_ms + 1
        slider_marks = build_slider_marks(slider_min, slider_max, tz, max_marks=8)

        files_by_plant = index_data.get("files_by_plant", {}) or {}
        status_text = (
            f"Historical files loaded: {plant_name('lib')}={len(files_by_plant.get('lib', []))} "
            f"{plant_name('vrfb')}={len(files_by_plant.get('vrfb', []))} | "
            f"Detected range: {_format_epoch_label(global_start_ms)} -> {_format_epoch_label(global_end_ms)}"
        )

        return (
            index_data,
            slider_min,
            slider_max,
            selected_range,
            slider_marks,
            False,
            status_text,
        )

    @app.callback(
        [
            Output("plots-range-label", "children"),
            Output("plots-timeline-graph", "figure"),
            Output("plots-range-meta-store", "data"),
        ],
        [Input("main-tabs", "value"), Input("plots-index-store", "data"), Input("plots-range-slider", "value")],
        prevent_initial_call=False,
    )
    def update_historical_range_view(active_tab, index_data, selected_range):
        if active_tab != "plots":
            raise PreventUpdate

        if not isinstance(index_data, dict) or not index_data.get("has_data"):
            return "Range: n/a", _empty_history_timeline_figure("No historical measurements found."), None

        clamped_range = clamp_epoch_range(
            selected_range,
            index_data.get("global_start_ms"),
            index_data.get("global_end_ms"),
        )
        if not clamped_range:
            return "Range: n/a", _empty_history_timeline_figure("No historical measurements found."), None

        range_label = f"Range: {_format_epoch_label(clamped_range[0])} -> {_format_epoch_label(clamped_range[1])}"
        timeline_fig = _build_history_timeline_figure(index_data, clamped_range)
        range_meta = _range_meta_for_selection(clamped_range)
        return range_label, timeline_fig, range_meta

    @app.callback(
        [Output("plots-graph-lib", "figure"), Output("plots-graph-vrfb", "figure")],
        [Input("main-tabs", "value"), Input("plots-index-store", "data"), Input("plots-range-slider", "value")],
        prevent_initial_call=False,
    )
    def update_historical_plots(active_tab, index_data, selected_range):
        if active_tab != "plots":
            raise PreventUpdate

        if not isinstance(index_data, dict) or not index_data.get("has_data"):
            return (
                _empty_history_plant_figure("lib", "No historical LIB measurements found."),
                _empty_history_plant_figure("vrfb", "No historical VRFB measurements found."),
            )

        domain_start = index_data.get("global_start_ms")
        domain_end = index_data.get("global_end_ms")
        clamped_range = clamp_epoch_range(selected_range, domain_start, domain_end)
        if not clamped_range:
            return (
                _empty_history_plant_figure("lib", "No historical LIB measurements found."),
                _empty_history_plant_figure("vrfb", "No historical VRFB measurements found."),
            )

        def build_plant_fig(plant_id):
            measurements_df = _load_history_df_for_plant(index_data, plant_id, clamped_range)
            if measurements_df.empty:
                return _empty_history_plant_figure(plant_id, f"No {plant_name(plant_id)} data in selected range.")
            return create_plant_figure(
                plant_id,
                plant_name,
                pd.DataFrame(),
                measurements_df,
                uirevision_key=f"plots:{plant_id}:{clamped_range[0]}:{clamped_range[1]}",
                tz=tz,
                plot_theme=plot_theme,
                trace_colors=trace_colors,
                voltage_autorange_padding_kv=_voltage_padding_kv_for_plant(plant_id),
            )

        return build_plant_fig("lib"), build_plant_fig("vrfb")

    def _download_history_csv_payload(plant_id, index_data, selected_range, range_meta):
        domain_start = (index_data or {}).get("global_start_ms")
        domain_end = (index_data or {}).get("global_end_ms")
        clamped_range = clamp_epoch_range(selected_range, domain_start, domain_end)
        if not clamped_range:
            clamped_range = [0, 0]
        df = _load_history_df_for_plant(index_data or {}, plant_id, clamped_range)
        csv_df = serialize_measurements_for_download(df, tz)

        start_token = (range_meta or {}).get("start_token")
        end_token = (range_meta or {}).get("end_token")
        if not start_token or not end_token:
            fallback_meta = _range_meta_for_selection(clamped_range)
            start_token = (fallback_meta or {}).get("start_token", "start")
            end_token = (fallback_meta or {}).get("end_token", "end")

        filename = f"measurements_{plant_id}_{start_token}_{end_token}.csv"
        return dcc.send_data_frame(csv_df.to_csv, filename, index=False)

    @app.callback(
        Output("plots-download-csv-lib", "data"),
        Input("plots-download-csv-lib-btn", "n_clicks"),
        [State("plots-index-store", "data"), State("plots-range-slider", "value"), State("plots-range-meta-store", "data")],
        prevent_initial_call=True,
    )
    def download_historical_csv_lib(n_clicks, index_data, selected_range, range_meta):
        if not n_clicks:
            raise PreventUpdate
        return _download_history_csv_payload("lib", index_data, selected_range, range_meta)

    @app.callback(
        Output("plots-download-csv-vrfb", "data"),
        Input("plots-download-csv-vrfb-btn", "n_clicks"),
        [State("plots-index-store", "data"), State("plots-range-slider", "value"), State("plots-range-meta-store", "data")],
        prevent_initial_call=True,
    )
    def download_historical_csv_vrfb(n_clicks, index_data, selected_range, range_meta):
        if not n_clicks:
            raise PreventUpdate
        return _download_history_csv_payload("vrfb", index_data, selected_range, range_meta)

    @app.callback(
        Output("log-file-selector", "options"),
        Input("interval-component", "n_intervals"),
    )
    def update_log_file_options(n_intervals):
        options = [{"label": "Today", "value": "today"}]
        logs_dir = get_logs_dir(base_dir)
        today_path = os.path.abspath(get_today_log_file_path(base_dir, tz))
        try:
            if os.path.exists(logs_dir):
                log_files = []
                for filename in os.listdir(logs_dir):
                    if not filename.endswith(".log"):
                        continue
                    full_path = os.path.join(logs_dir, filename)
                    if os.path.abspath(full_path) == today_path:
                        continue
                    try:
                        date_str = filename.split("_", 1)[0]
                        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
                        log_files.append((date_obj, date_obj.strftime("%Y-%m-%d"), full_path))
                    except (ValueError, IndexError):
                        log_files.append((None, filename, full_path))

                dated = [item for item in log_files if item[0] is not None]
                undated = [item for item in log_files if item[0] is None]
                dated.sort(key=lambda item: item[0], reverse=True)
                undated.sort(key=lambda item: item[1], reverse=True)

                for _, display_name, full_path in dated + undated:
                    options.append({"label": display_name, "value": full_path})
        except Exception as exc:
            logging.error("Dashboard: failed to scan log files: %s", exc)
        return options

    @app.callback(
        [Output("logs-display", "children"), Output("log-file-path", "children")],
        [Input("interval-component", "n_intervals"), Input("log-file-selector", "value")],
        prevent_initial_call=False,
    )
    def update_logs_display(n_intervals, selected_file):
        ctx = callback_context
        trigger_id = ctx.triggered[0]["prop_id"].split(".")[0] if ctx.triggered else None
        selected = selected_file or "today"
        if selected == "current_session":
            selected = "today"

        if trigger_id == "interval-component" and selected != "today":
            raise PreventUpdate

        if selected == "today":
            log_file_path = get_today_log_file_path(base_dir, tz)
            today_file_exists = os.path.exists(log_file_path)
            file_content = read_log_tail(log_file_path, max_lines=1000)
            formatted = parse_and_format_historical_logs(file_content)
            if not formatted:
                empty_text = "No parseable log entries." if today_file_exists else "No logs yet."
                formatted = [html.Div(empty_text, className="logs-empty")]
            return formatted, f"File: {log_file_path}"

        try:
            with open(selected, "r", encoding="utf-8", errors="replace") as handle:
                file_content = handle.read()
            formatted = parse_and_format_historical_logs(file_content)
            if not formatted:
                formatted = [html.Div("No parseable log entries.", className="logs-empty")]
            return formatted, f"File: {selected}"
        except Exception as exc:
            logging.error("Dashboard: failed reading log file %s: %s", selected, exc)
            message = f"Error reading log file: {exc}"
            return [html.Div(message, className="logs-error")], f"Error: {selected}"

    def run_app():
        app.run(host="0.0.0.0", port="8050", debug=False, threaded=True)

    thread = threading.Thread(target=run_app, daemon=True)
    thread.start()

    while not shared_data["shutdown_event"].is_set():
        time.sleep(1)

    logging.info("Dashboard agent stopped.")


if __name__ == "__main__":
    pass
