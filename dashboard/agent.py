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

from control.command_runtime import enqueue_control_command
from dashboard.command_intents import command_intent_from_control_trigger, confirmed_toggle_intent_from_request
from dashboard.settings_intents import (
    api_connection_intent_from_trigger,
    manual_settings_intent_from_trigger,
    posting_intent_from_trigger,
)
from dashboard.control_health import (
    summarize_control_engine_status,
    summarize_control_queue_status,
    summarize_dispatch_write_status,
    summarize_plant_modbus_health,
)
from dashboard.history import (
    build_slider_marks,
    clamp_epoch_range,
    load_cropped_measurements_for_range,
    scan_measurement_history_index,
    serialize_measurements_for_download,
)
from dashboard.layout import build_dashboard_layout
from dashboard.logs import get_logs_dir, get_today_log_file_path, parse_and_format_historical_logs, read_log_tail
from dashboard.plotting import (
    DEFAULT_PLOT_THEME,
    DEFAULT_TRACE_COLORS,
    apply_figure_theme,
    create_plant_figure,
    create_manual_series_figure,
)
from dashboard.ui_state import (
    get_plant_power_toggle_state,
    get_recording_toggle_state,
    is_observed_state_effectively_stale,
    resolve_click_feedback_transition_state,
    resolve_runtime_transition_state,
)
from dashboard.settings_ui_state import (
    api_connection_controls_state,
    api_connection_display_state,
    manual_series_controls_state,
    manual_series_display_state,
    posting_controls_state,
    posting_display_state,
    resolve_command_click_feedback_state,
)
import scheduling.manual_schedule_manager as msm
from measurement.storage import MEASUREMENT_COLUMNS
from runtime.contracts import sanitize_plant_name
from runtime.paths import get_assets_dir, get_data_dir, get_project_root
from scheduling.runtime import build_effective_schedule_frame
from settings.command_runtime import enqueue_settings_command
from runtime.shared_state import snapshot_locked
from time_utils import get_config_tz, normalize_datetime_series, normalize_schedule_index, normalize_timestamp_value, now_tz


def dashboard_agent(config, shared_data):
    """Dash dashboard with global source/transport and per-plant controls/plots."""
    logging.info("Dashboard agent started.")

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
    ui_transition_feedback_hold_s = 2.0
    ui_confirm_toggle_min_hold_s = max(ui_transition_feedback_hold_s, float(config["MEASUREMENT_PERIOD_S"]))
    ui_confirm_toggle_max_hold_s = max(6.0, ui_confirm_toggle_min_hold_s + float(config["MEASUREMENT_PERIOD_S"]))

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

    def _round_up_to_next_10min(now_value=None):
        ts = normalize_timestamp_value(now_value or now_tz(config), tz)
        if pd.isna(ts):
            ts = normalize_timestamp_value(now_tz(config), tz)
        ts = pd.Timestamp(ts)
        base = ts.replace(second=0, microsecond=0)
        minute_mod = int(base.minute) % 10
        needs_advance = (minute_mod != 0) or (ts.second != 0) or (ts.microsecond != 0)
        if not needs_advance:
            return base
        add_minutes = 10 - minute_mod if minute_mod != 0 else 10
        return base + pd.Timedelta(minutes=add_minutes)

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
            draft_norm = msm.ensure_manual_series_terminal_duplicate_row(draft_df, timezone_name=config.get("TIMEZONE_NAME"))
            applied_norm = msm.ensure_manual_series_terminal_duplicate_row(applied_df, timezone_name=config.get("TIMEZONE_NAME"))
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

    def _binary_toggle_classes(active_side, *, semantic=True):
        positive = ["toggle-option"]
        negative = ["toggle-option"]
        if semantic:
            positive.append("toggle-option--positive")
            negative.append("toggle-option--negative")
        if active_side == "positive":
            positive.append("active")
        elif active_side == "negative":
            negative.append("active")
        return " ".join(positive), " ".join(negative)

    def _toggle_confirm_request_for_transport(*, requested_side):
        side = "positive" if str(requested_side) == "positive" else "negative"
        requested_mode = "local" if side == "positive" else "remote"
        return {
            "toggle_key": "transport",
            "resource_key": None,
            "requested_side": side,
            "title": "Confirm Transport Switch",
            "message": "Switching transport mode will safe-stop both plants, stop recording, and clear plot caches. Continue?",
            "requested_mode": requested_mode,
        }

    def _toggle_confirm_request_for_plant_power(*, plant_id, requested_side):
        side = "positive" if str(requested_side) == "positive" else "negative"
        plant_id = str(plant_id)
        plant_label = plant_name(plant_id)
        if side == "positive":
            title = "Confirm Plant Start"
            message = f"Start {plant_label} plant operation?"
        else:
            title = "Confirm Plant Stop"
            message = f"Safe-stop {plant_label} plant operation?"
        return {
            "toggle_key": "plant_power",
            "resource_key": plant_id,
            "requested_side": side,
            "title": title,
            "message": message,
        }

    def _toggle_action_feedback_state(
        action_data,
        *,
        toggle_key,
        resource_key=None,
        current_server_state=None,
        min_hold_s=None,
        max_hold_s=None,
    ):
        if not isinstance(action_data, dict):
            return None
        if str(action_data.get("toggle_key") or "") != str(toggle_key):
            return None
        if resource_key is not None and str(action_data.get("resource_key")) != str(resource_key):
            return None
        try:
            ts_ms = int(action_data.get("timestamp_ms"))
        except (TypeError, ValueError):
            return None
        now_ts = now_tz(config)
        age_s = (float(now_ts.timestamp()) * 1000.0 - float(ts_ms)) / 1000.0
        if age_s < 0:
            age_s = 0.0
        min_hold = float(ui_transition_feedback_hold_s if min_hold_s is None else min_hold_s)
        max_hold = float(ui_transition_feedback_hold_s if max_hold_s is None else max_hold_s)
        if max_hold < min_hold:
            max_hold = min_hold
        if age_s > max_hold:
            return None
        server_state_before = action_data.get("server_state_before", None)
        if age_s >= min_hold and current_server_state is not None and server_state_before is not None:
            if str(current_server_state) != str(server_state_before):
                return None
        side = str(action_data.get("requested_side") or "")
        return {
            "requested_side": side,
            "age_s": age_s,
            "timestamp_ms": ts_ms,
            "server_state_before": server_state_before,
        }

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
        _round_up_to_next_10min(now_tz(config)),
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
            Output("transport-local-btn", "children"),
            Output("transport-local-btn", "className"),
            Output("transport-local-btn", "disabled"),
            Output("transport-remote-btn", "children"),
            Output("transport-remote-btn", "className"),
            Output("transport-remote-btn", "disabled"),
        ],
        [
            Input("interval-component", "n_intervals"),
            Input("control-action", "data"),
            Input("toggle-confirm-action", "data"),
        ],
        prevent_initial_call=False,
    )
    def render_transport_toggle(_n_intervals, _control_action, toggle_confirm_action):
        with shared_data["lock"]:
            stored_mode = str(shared_data.get("transport_mode", "local") or "local")
            transport_switching = bool(shared_data.get("transport_switching", False))

        feedback = _toggle_action_feedback_state(
            toggle_confirm_action,
            toggle_key="transport",
            current_server_state=stored_mode,
            min_hold_s=ui_confirm_toggle_min_hold_s,
            max_hold_s=ui_confirm_toggle_max_hold_s,
        )
        if feedback:
            requested_side = feedback.get("requested_side")
            if requested_side == "positive":
                active_side = "positive"
                local_label = "Switching to Local..."
                remote_label = "Remote"
            else:
                active_side = "negative"
                local_label = "Local"
                remote_label = "Switching to Remote..."
            local_class, remote_class = _binary_toggle_classes(active_side, semantic=False)
            return (
                "local" if active_side == "positive" else "remote",
                local_label,
                local_class,
                True,
                remote_label,
                remote_class,
                True,
            )

        active_side = "negative" if stored_mode == "remote" else "positive"
        local_class, remote_class = _binary_toggle_classes(active_side, semantic=False)
        local_disabled = bool(transport_switching or stored_mode == "local")
        remote_disabled = bool(transport_switching or stored_mode == "remote")
        return (
            stored_mode,
            "Local",
            local_class,
            local_disabled,
            "Remote",
            remote_class,
            remote_disabled,
        )

    @app.callback(
        [
            Output("toggle-confirm-modal", "className"),
            Output("toggle-confirm-modal-title", "children"),
            Output("toggle-confirm-modal-text", "children"),
            Output("toggle-confirm-request", "data"),
        ],
        [
            Input("transport-local-btn", "n_clicks"),
            Input("transport-remote-btn", "n_clicks"),
            Input("start-lib", "n_clicks"),
            Input("stop-lib", "n_clicks"),
            Input("start-vrfb", "n_clicks"),
            Input("stop-vrfb", "n_clicks"),
            Input("toggle-confirm-cancel", "n_clicks"),
            Input("toggle-confirm-confirm", "n_clicks"),
        ],
        [State("toggle-confirm-request", "data")],
        prevent_initial_call=False,
    )
    def handle_toggle_confirm_modal(
        _transport_local_clicks,
        _transport_remote_clicks,
        _start_lib_clicks,
        _stop_lib_clicks,
        _start_vrfb_clicks,
        _stop_vrfb_clicks,
        _cancel_clicks,
        _confirm_clicks,
        current_request,
    ):
        ctx = callback_context
        hidden_class = "modal-overlay hidden"
        open_class = "modal-overlay"
        default_title = "Confirm Action"
        default_text = ""

        if not ctx.triggered:
            return hidden_class, default_title, default_text, None

        trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]
        if trigger_id in {"toggle-confirm-cancel", "toggle-confirm-confirm"}:
            return hidden_class, default_title, default_text, None

        with shared_data["lock"]:
            stored_mode = str(shared_data.get("transport_mode", "local") or "local")

        if trigger_id in {"transport-local-btn", "transport-remote-btn"}:
            requested_side = "positive" if trigger_id == "transport-local-btn" else "negative"
            requested_mode = "local" if requested_side == "positive" else "remote"
            if requested_mode == stored_mode:
                return hidden_class, default_title, default_text, current_request
            req = _toggle_confirm_request_for_transport(requested_side=requested_side)
            return open_class, req["title"], req["message"], req

        plant_trigger_map = {
            "start-lib": ("lib", "positive"),
            "stop-lib": ("lib", "negative"),
            "start-vrfb": ("vrfb", "positive"),
            "stop-vrfb": ("vrfb", "negative"),
        }
        mapped = plant_trigger_map.get(trigger_id)
        if mapped:
            plant_id, requested_side = mapped
            req = _toggle_confirm_request_for_plant_power(plant_id=plant_id, requested_side=requested_side)
            return open_class, req["title"], req["message"], req

        return hidden_class, default_title, default_text, current_request

    @app.callback(
        [
            Output("api-posting-toggle-store", "data"),
            Output("api-posting-enable-btn", "children"),
            Output("api-posting-enable-btn", "className"),
            Output("api-posting-disable-btn", "children"),
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
        enable_class, disable_class = _binary_toggle_classes("positive" if visual_enabled else "negative")
        return (
            bool(policy_enabled),
            controls["enable_label"],
            enable_class,
            controls["disable_label"],
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
            Output("set-password-btn", "className"),
            Output("set-password-btn", "disabled"),
            Output("disconnect-api-btn", "children"),
            Output("disconnect-api-btn", "className"),
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
        active_side = "positive" if display_state in {"connected", "connecting"} else "negative"
        connect_class, disconnect_class = _binary_toggle_classes(active_side)
        return (
            controls["connect_label"],
            connect_class,
            bool(controls["connect_disabled"]),
            controls["disconnect_label"],
            disconnect_class,
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
        [Output("control-action", "data"), Output("toggle-confirm-action", "data")],
        [
            Input("dispatch-enable-lib", "n_clicks"),
            Input("dispatch-disable-lib", "n_clicks"),
            Input("record-lib", "n_clicks"),
            Input("record-stop-lib", "n_clicks"),
            Input("dispatch-enable-vrfb", "n_clicks"),
            Input("dispatch-disable-vrfb", "n_clicks"),
            Input("record-vrfb", "n_clicks"),
            Input("record-stop-vrfb", "n_clicks"),
            Input("bulk-control-confirm", "n_clicks"),
            Input("toggle-confirm-confirm", "n_clicks"),
        ],
        [State("bulk-control-request", "data"), State("toggle-confirm-request", "data")],
        prevent_initial_call=True,
    )
    def handle_controls(*args):
        ctx = callback_context
        if not ctx.triggered:
            raise PreventUpdate

        trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]
        bulk_request = args[-2]
        toggle_confirm_request = args[-1]

        if trigger_id == "toggle-confirm-confirm":
            req = dict(toggle_confirm_request or {})
            intent = confirmed_toggle_intent_from_request(req)
            if intent is None:
                raise PreventUpdate
            baseline_server_state = None
            toggle_key = str(req.get("toggle_key") or "")
            if toggle_key == "transport":
                with shared_data["lock"]:
                    baseline_server_state = str(shared_data.get("transport_mode", "local") or "local")
            elif toggle_key == "plant_power":
                plant_id = str(req.get("resource_key") or "")
                with shared_data["lock"]:
                    transition_state = str((shared_data.get("plant_transition_by_plant", {}) or {}).get(plant_id, "unknown"))
                    observed = dict((shared_data.get("plant_observed_state_by_plant", {}) or {}).get(plant_id, {}) or {})
                enable_state = None if bool(observed.get("stale", True)) else observed.get("enable_state")
                baseline_server_state = resolve_runtime_transition_state(transition_state, enable_state)
            status = _enqueue_dashboard_control_intent(intent, trigger_id=trigger_id)
            toggle_action_data = {
                "toggle_key": req.get("toggle_key"),
                "resource_key": req.get("resource_key"),
                "requested_side": req.get("requested_side"),
                "timestamp_ms": int(time.time() * 1000),
                "server_state_before": baseline_server_state,
            }
            return _command_status_action_token(status), toggle_action_data

        intent = command_intent_from_control_trigger(trigger_id, bulk_request=bulk_request)
        if intent is None:
            raise PreventUpdate

        status = _enqueue_dashboard_control_intent(intent, trigger_id=trigger_id)
        return _command_status_action_token(status), dash.no_update

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
            active_cls, inactive_cls = _binary_toggle_classes("positive" if bool(control_state["active_visual"]) else "negative")
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
        draft_snapshot = snapshot_locked(shared_data, lambda data: dict(data.get("manual_schedule_draft_series_df_by_key", {})))
        intent = manual_settings_intent_from_trigger(
            trigger_id,
            draft_series_by_key=draft_snapshot,
            tz=tz,
        )
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
            start_ts = _round_up_to_next_10min(now_tz(config))
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
                kind = "end" if str(item.get("kind", "value") or "value").strip().lower() == "end" else "value"
                item["hours"] = int(item.get("hours", 0) or 0)
                item["minutes"] = int(item.get("minutes", 0) or 0)
                item["seconds"] = int(item.get("seconds", 0) or 0)
                item["kind"] = kind
                if kind == "end":
                    item["setpoint"] = None
                else:
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

        def _sanitize_editor_rows(rows_to_sanitize):
            return msm._normalize_editor_rows(rows_to_sanitize)

        _is_end_row = msm._is_end_editor_row

        def _find_last_value_index(items):
            for rev_idx in range(len(items) - 1, -1, -1):
                if not _is_end_row(items[rev_idx]):
                    return rev_idx
            return None

        def _row_offset_seconds(row):
            row = dict(row or {})
            return int(row.get("hours", 0) or 0) * 3600 + int(row.get("minutes", 0) or 0) * 60 + int(row.get("seconds", 0) or 0)

        def _value_template_for_insert(items, preferred_idx=None):
            if not items:
                return {"hours": 0, "minutes": 0, "seconds": 0, "setpoint": 0.0, "kind": "value"}
            if preferred_idx is not None and 0 <= preferred_idx < len(items) and not _is_end_row(items[preferred_idx]):
                base = dict(items[preferred_idx])
            else:
                last_value_idx = _find_last_value_index(items)
                base = dict(items[last_value_idx]) if last_value_idx is not None else {"hours": 0, "minutes": 0, "seconds": 0, "setpoint": 0.0}
            base["kind"] = "value"
            try:
                base["setpoint"] = float(base.get("setpoint", 0.0) or 0.0)
            except (TypeError, ValueError):
                base["setpoint"] = 0.0
            return base

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
            if delete_idx == 0:
                return dash.no_update, None, "Delete request ignored: first row cannot be removed."
            rows.pop(delete_idx)
            if rows and all(_is_end_row(row) for row in rows):
                rows = []
            try:
                rows = _sanitize_editor_rows(rows)
            except Exception as exc:
                return dash.no_update, None, f"Delete request ignored: {exc}"
            return rows, None, f"Deleted breakpoint row {delete_idx + 1}."

        if trigger_id == "manual-editor-add-first-row-btn":
            if not rows:
                return _sanitize_editor_rows([{"hours": 0, "minutes": 0, "seconds": 0, "setpoint": 0.0, "kind": "value"}]), None, dash.no_update
            insert_idx = len(rows)
            if _is_end_row(rows[-1]):
                insert_idx = len(rows) - 1
            last = _value_template_for_insert(rows)
            rows.insert(insert_idx, last)
            return _sanitize_editor_rows(rows), None, dash.no_update

        if isinstance(trigger_id, dict) and trigger_id.get("type") == "manual-row-add":
            try:
                idx = int(trigger_id.get("index", -1))
            except (TypeError, ValueError):
                idx = -1
            if idx < 0 or idx >= len(rows):
                raise PreventUpdate
            insert_at = idx if _is_end_row(rows[idx]) else idx + 1
            template_idx = idx if not _is_end_row(rows[idx]) else (idx - 1)
            new_row = _value_template_for_insert(rows, preferred_idx=template_idx)
            rows.insert(insert_at, new_row)
            return _sanitize_editor_rows(rows), None, dash.no_update

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
                prev_kind = "end" if str((prev or {}).get("kind", "value")) == "end" else "value"
                setpoint_value = None
                if prev_kind != "end":
                    if idx < len(row_setpoints or []) and (row_setpoints or [])[idx] is not None:
                        try:
                            setpoint_value = float((row_setpoints or [])[idx])
                        except (TypeError, ValueError):
                            setpoint_value = float(prev.get("setpoint", 0.0) or 0.0)
                    else:
                        setpoint_value = float(prev.get("setpoint", 0.0) or 0.0)
                new_rows.append(
                    {
                        "hours": 0 if idx == 0 else int((row_hours or [])[idx] if idx < len(row_hours or []) and (row_hours or [])[idx] is not None else prev.get("hours", 0) or 0),
                        "minutes": 0 if idx == 0 else int((row_minutes or [])[idx] if idx < len(row_minutes or []) and (row_minutes or [])[idx] is not None else prev.get("minutes", 0) or 0),
                        "seconds": 0 if idx == 0 else int((row_seconds or [])[idx] if idx < len(row_seconds or []) and (row_seconds or [])[idx] is not None else prev.get("seconds", 0) or 0),
                        "setpoint": setpoint_value,
                        "kind": prev_kind,
                    }
                )
            if new_rows:
                new_rows[0]["hours"] = 0
                new_rows[0]["minutes"] = 0
                new_rows[0]["seconds"] = 0
            return _sanitize_editor_rows(new_rows), dash.no_update, dash.no_update

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
            is_end = str(row.get("kind", "value")) == "end"
            if is_end:
                setpoint_component = dcc.Input(
                    id={"type": "manual-row-setpoint", "index": idx},
                    type="text",
                    className="form-control",
                    value="end",
                    disabled=True,
                    style={**compact_setpoint_style, "fontWeight": "700"},
                )
            else:
                setpoint_component = dcc.Input(
                    id={"type": "manual-row-setpoint", "index": idx},
                    type="number",
                    className="form-control",
                    step="any",
                    value=row.get("setpoint", 0.0),
                    style=compact_setpoint_style,
                )
            if is_end:
                actions_component = html.Div(
                    style={**action_group_style, "minWidth": "60px"},
                    children=[],
                )
            else:
                actions_component = html.Div(
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
                        *(
                            []
                            if idx == 0
                            else [
                                html.Button(
                                    "-",
                                    id={"type": "manual-row-del", "index": idx},
                                    className="btn btn-danger",
                                    n_clicks=0,
                                    title="Delete row",
                                    style=row_action_btn_style,
                                )
                            ]
                        ),
                    ],
                )
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
                        setpoint_component,
                        actions_component,
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
            row_list = list(rows or [])
            value_count = sum(1 for row in row_list if str((row or {}).get("kind", "value")) != "end")
            has_end = any(str((row or {}).get("kind", "value")) == "end" for row in row_list)
            meta = msm.MANUAL_SERIES_META.get(series_key or "", {})
            label = meta.get("label", str(series_key or "schedule"))
            suffix = " + end" if has_end else ""
            return f"{label}: staged {value_count} breakpoint(s){suffix}."
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
            Output("operator-plant-summary-table", "children"),
            Output("status-lib", "children"),
            Output("status-vrfb", "children"),
            Output("graph-lib", "figure"),
            Output("graph-vrfb", "figure"),
            Output("start-lib", "children"),
            Output("start-lib", "className"),
            Output("start-lib", "disabled"),
            Output("stop-lib", "children"),
            Output("stop-lib", "className"),
            Output("stop-lib", "disabled"),
            Output("dispatch-enable-lib", "children"),
            Output("dispatch-enable-lib", "className"),
            Output("dispatch-enable-lib", "disabled"),
            Output("dispatch-disable-lib", "children"),
            Output("dispatch-disable-lib", "className"),
            Output("dispatch-disable-lib", "disabled"),
            Output("record-lib", "children"),
            Output("record-lib", "className"),
            Output("record-lib", "disabled"),
            Output("record-stop-lib", "children"),
            Output("record-stop-lib", "className"),
            Output("record-stop-lib", "disabled"),
            Output("start-vrfb", "children"),
            Output("start-vrfb", "className"),
            Output("start-vrfb", "disabled"),
            Output("stop-vrfb", "children"),
            Output("stop-vrfb", "className"),
            Output("stop-vrfb", "disabled"),
            Output("dispatch-enable-vrfb", "children"),
            Output("dispatch-enable-vrfb", "className"),
            Output("dispatch-enable-vrfb", "disabled"),
            Output("dispatch-disable-vrfb", "children"),
            Output("dispatch-disable-vrfb", "className"),
            Output("dispatch-disable-vrfb", "disabled"),
            Output("record-vrfb", "children"),
            Output("record-vrfb", "className"),
            Output("record-vrfb", "disabled"),
            Output("record-stop-vrfb", "children"),
            Output("record-stop-vrfb", "className"),
            Output("record-stop-vrfb", "disabled"),
        ],
        [
            Input("interval-component", "n_intervals"),
            Input("control-action", "data"),
            Input("toggle-confirm-action", "data"),
            Input("dispatch-enable-lib", "n_clicks_timestamp"),
            Input("dispatch-disable-lib", "n_clicks_timestamp"),
            Input("record-lib", "n_clicks_timestamp"),
            Input("record-stop-lib", "n_clicks_timestamp"),
            Input("dispatch-enable-vrfb", "n_clicks_timestamp"),
            Input("dispatch-disable-vrfb", "n_clicks_timestamp"),
            Input("record-vrfb", "n_clicks_timestamp"),
            Input("record-stop-vrfb", "n_clicks_timestamp"),
        ],
    )
    def update_status_and_graphs(
        n_intervals,
        control_action,
        toggle_confirm_action,
        dispatch_enable_lib_click_ts_ms,
        dispatch_disable_lib_click_ts_ms,
        record_lib_click_ts_ms,
        record_stop_lib_click_ts_ms,
        dispatch_enable_vrfb_click_ts_ms,
        dispatch_disable_vrfb_click_ts_ms,
        record_vrfb_click_ts_ms,
        record_stop_vrfb_click_ts_ms,
    ):
        with shared_data["lock"]:
            transport_mode = shared_data.get("transport_mode", "local")
            scheduler_running = dict(shared_data.get("scheduler_running_by_plant", {}))
            transition_by_plant = dict(shared_data.get("plant_transition_by_plant", {}))
            plant_operating_state_by_plant = dict(shared_data.get("plant_operating_state_by_plant", {}))
            recording_files = dict(shared_data.get("measurements_filename_by_plant", {}))
            observed_state_by_plant = dict(shared_data.get("plant_observed_state_by_plant", {}))
            dispatch_write_status_by_plant = dict(shared_data.get("dispatch_write_status_by_plant", {}))
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
        observed_effective_stale_by_plant = {}
        for plant_id in plant_ids:
            observed = dict(observed_state_by_plant.get(plant_id, {}) or {})
            effective_stale = is_observed_state_effectively_stale(
                observed,
                now_ts=status_now,
            )
            observed_effective_stale_by_plant[plant_id] = bool(effective_stale)
            if effective_stale:
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
        click_feedback_by_plant = {}
        for plant_id in plant_ids:
            confirm_feedback = _toggle_action_feedback_state(
                toggle_confirm_action,
                toggle_key="plant_power",
                resource_key=plant_id,
                current_server_state=_engine_state_by_plant.get(plant_id, "unknown"),
                min_hold_s=ui_confirm_toggle_min_hold_s,
                max_hold_s=ui_confirm_toggle_max_hold_s,
            )
            if not confirm_feedback:
                click_feedback_by_plant[plant_id] = None
                continue
            click_feedback_by_plant[plant_id] = (
                "starting" if str(confirm_feedback.get("requested_side")) == "positive" else "stopping"
            )
        record_click_feedback_by_plant = {
            "lib": resolve_click_feedback_transition_state(
                start_click_ts_ms=record_lib_click_ts_ms,
                stop_click_ts_ms=record_stop_lib_click_ts_ms,
                now_ts=status_now,
                hold_seconds=ui_transition_feedback_hold_s,
            ),
            "vrfb": resolve_click_feedback_transition_state(
                start_click_ts_ms=record_vrfb_click_ts_ms,
                stop_click_ts_ms=record_stop_vrfb_click_ts_ms,
                now_ts=status_now,
                hold_seconds=ui_transition_feedback_hold_s,
            ),
        }
        dispatch_click_feedback_by_plant = {
            "lib": resolve_command_click_feedback_state(
                positive_click_ts_ms=dispatch_enable_lib_click_ts_ms,
                negative_click_ts_ms=dispatch_disable_lib_click_ts_ms,
                positive_state="starting",
                negative_state="pausing",
                now_ts=status_now,
                hold_seconds=ui_transition_feedback_hold_s,
            ),
            "vrfb": resolve_command_click_feedback_state(
                positive_click_ts_ms=dispatch_enable_vrfb_click_ts_ms,
                negative_click_ts_ms=dispatch_disable_vrfb_click_ts_ms,
                positive_state="starting",
                negative_state="pausing",
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
                ),
                className="public-summary-measurement-cell",
            )

        def _latest_measurements_row(plant_id):
            measurements_df = measurements_map.get(plant_id, pd.DataFrame())
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

        status_window_start = status_now.replace(hour=0, minute=0, second=0, microsecond=0)
        status_window_end = status_window_start + timedelta(days=2)

        def plant_status_text(plant_id):
            recording = recording_files.get(plant_id)
            runtime_state = runtime_state_by_plant.get(plant_id, "unknown")
            effective_stale = bool(observed_effective_stale_by_plant.get(plant_id, True))
            physical_state = (
                "unknown"
                if effective_stale
                else str(plant_operating_state_by_plant.get(plant_id, runtime_state) or "unknown")
            )
            dispatch_enabled = bool(scheduler_running.get(plant_id, False))
            rec_text = f"Recording: On ({os.path.basename(recording)})" if recording else "Recording: Off"
            observed = dict(observed_state_by_plant.get(plant_id, {}) or {})
            observed["stale"] = effective_stale
            dispatch_write_state = dict(dispatch_write_status_by_plant.get(plant_id, {}) or {})
            health_lines = summarize_plant_modbus_health(observed, status_now)
            dispatch_lines = summarize_dispatch_write_status(dispatch_write_state, dispatch_enabled=dispatch_enabled)
            rows = [
                html.Div(
                    (
                        f"{plant_name(plant_id)} | Plant: {physical_state.capitalize()} | "
                        f"Control: {runtime_state.capitalize()} | {rec_text}"
                    ),
                    className="status-text",
                )
            ]
            rows.extend(html.Div(text, className="status-text") for text in dispatch_lines)
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

        def _dispatch_toggle_state(dispatch_enabled, click_feedback_state=None):
            feedback = str(click_feedback_state or "").lower()
            if feedback == "starting":
                return {
                    "positive_label": "Starting",
                    "negative_label": "Pause",
                    "positive_disabled": True,
                    "negative_disabled": True,
                    "active_side": "positive",
                }
            if feedback == "pausing":
                return {
                    "positive_label": "Dispatch",
                    "negative_label": "Pausing...",
                    "positive_disabled": True,
                    "negative_disabled": True,
                    "active_side": "negative",
                }
            enabled = bool(dispatch_enabled)
            return {
                "positive_label": "Dispatching" if enabled else "Dispatch",
                "negative_label": "Pause" if enabled else "Paused",
                "positive_disabled": enabled,
                "negative_disabled": not enabled,
                "active_side": "positive" if enabled else "negative",
            }

        lib_power_controls = get_plant_power_toggle_state(runtime_state_by_plant.get("lib", "unknown"))
        lib_record_controls = get_recording_toggle_state(
            bool(recording_files.get("lib")),
            click_feedback_state=record_click_feedback_by_plant.get("lib"),
        )
        lib_dispatch_controls = _dispatch_toggle_state(
            scheduler_running.get("lib", False),
            click_feedback_state=dispatch_click_feedback_by_plant.get("lib"),
        )
        lib_power_classes = _binary_toggle_classes(lib_power_controls["active_side"])
        lib_dispatch_classes = _binary_toggle_classes(lib_dispatch_controls["active_side"])
        lib_record_classes = _binary_toggle_classes(lib_record_controls["active_side"])

        vrfb_power_controls = get_plant_power_toggle_state(runtime_state_by_plant.get("vrfb", "unknown"))
        vrfb_record_controls = get_recording_toggle_state(
            bool(recording_files.get("vrfb")),
            click_feedback_state=record_click_feedback_by_plant.get("vrfb"),
        )
        vrfb_dispatch_controls = _dispatch_toggle_state(
            scheduler_running.get("vrfb", False),
            click_feedback_state=dispatch_click_feedback_by_plant.get("vrfb"),
        )
        vrfb_power_classes = _binary_toggle_classes(vrfb_power_controls["active_side"])
        vrfb_dispatch_classes = _binary_toggle_classes(vrfb_dispatch_controls["active_side"])
        vrfb_record_classes = _binary_toggle_classes(vrfb_record_controls["active_side"])

        return (
            api_inline,
            control_engine_inline,
            control_queue_inline,
            summary_table,
            plant_status_text("lib"),
            plant_status_text("vrfb"),
            lib_fig,
            vrfb_fig,
            lib_power_controls["positive_label"],
            lib_power_classes[0],
            bool(lib_power_controls["positive_disabled"]),
            lib_power_controls["negative_label"],
            lib_power_classes[1],
            bool(lib_power_controls["negative_disabled"]),
            lib_dispatch_controls["positive_label"],
            lib_dispatch_classes[0],
            bool(lib_dispatch_controls["positive_disabled"]),
            lib_dispatch_controls["negative_label"],
            lib_dispatch_classes[1],
            bool(lib_dispatch_controls["negative_disabled"]),
            lib_record_controls["positive_label"],
            lib_record_classes[0],
            bool(lib_record_controls["positive_disabled"]),
            lib_record_controls["negative_label"],
            lib_record_classes[1],
            bool(lib_record_controls["negative_disabled"]),
            vrfb_power_controls["positive_label"],
            vrfb_power_classes[0],
            bool(vrfb_power_controls["positive_disabled"]),
            vrfb_power_controls["negative_label"],
            vrfb_power_classes[1],
            bool(vrfb_power_controls["negative_disabled"]),
            vrfb_dispatch_controls["positive_label"],
            vrfb_dispatch_classes[0],
            bool(vrfb_dispatch_controls["positive_disabled"]),
            vrfb_dispatch_controls["negative_label"],
            vrfb_dispatch_classes[1],
            bool(vrfb_dispatch_controls["negative_disabled"]),
            vrfb_record_controls["positive_label"],
            vrfb_record_classes[0],
            bool(vrfb_record_controls["positive_disabled"]),
            vrfb_record_controls["negative_label"],
            vrfb_record_classes[1],
            bool(vrfb_record_controls["negative_disabled"]),
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
        index_data = scan_measurement_history_index(data_dir, plant_suffix_by_id, tz)

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
        logs_dir = get_logs_dir(project_dir)
        today_path = os.path.abspath(get_today_log_file_path(project_dir, tz))
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
            log_file_path = get_today_log_file_path(project_dir, tz)
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

    dashboard_host = str(config.get("DASHBOARD_PRIVATE_HOST", "127.0.0.1"))
    dashboard_port = int(config.get("DASHBOARD_PRIVATE_PORT", 8050))

    def run_app():
        app.run(host=dashboard_host, port=dashboard_port, debug=False, threaded=True)

    thread = threading.Thread(target=run_app, daemon=True)
    thread.start()

    while not shared_data["shutdown_event"].is_set():
        time.sleep(1)

    logging.info("Dashboard agent stopped.")


if __name__ == "__main__":
    pass
