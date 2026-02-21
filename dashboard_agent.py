import base64
import io
import logging
import os
import threading
import time
from datetime import datetime, timedelta

import dash
import pandas as pd
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, callback_context, html
from dash.exceptions import PreventUpdate

from dashboard_layout import build_dashboard_layout
from dashboard_logs import get_logs_dir, get_today_log_file_path, parse_and_format_historical_logs, read_log_tail
from dashboard_modbus_io import (
    read_enable_state as read_enable_state_io,
    send_setpoints as send_setpoints_io,
    set_enable as set_enable_io,
    wait_until_battery_power_below_threshold as wait_until_battery_power_below_threshold_io,
)
from dashboard_plotting import (
    DEFAULT_PLOT_THEME,
    DEFAULT_TRACE_COLORS,
    apply_figure_theme,
    create_plant_figure,
)
from dashboard_ui_state import get_plant_control_labels_and_disabled, resolve_runtime_transition_state
import manual_schedule_manager as msm
from runtime_contracts import resolve_modbus_endpoint, sanitize_plant_name
from schedule_runtime import resolve_schedule_setpoint
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

    raw_schedule_period_minutes = config.get("ISTENTORE_SCHEDULE_PERIOD_MINUTES", 15)
    try:
        schedule_period_minutes = float(raw_schedule_period_minutes)
        if schedule_period_minutes <= 0:
            raise ValueError("must be > 0")
    except (TypeError, ValueError):
        schedule_period_minutes = 15.0
    api_validity_window = pd.Timedelta(minutes=schedule_period_minutes)
    initial_snapshot = snapshot_locked(
        shared_data,
        lambda data: {
            "initial_source": data.get("active_schedule_source", "manual"),
            "initial_transport": data.get("transport_mode", "local"),
        },
    )
    initial_source = initial_snapshot["initial_source"]
    initial_transport = initial_snapshot["initial_transport"]

    brand_logo_src = app.get_asset_url("brand/Logotype i-STENTORE.png")

    plot_theme = dict(DEFAULT_PLOT_THEME)
    trace_colors = dict(DEFAULT_TRACE_COLORS)
    base_dir = os.path.dirname(__file__)

    def plant_name(plant_id):
        return str((plants_cfg.get(plant_id, {}) or {}).get("name", plant_id.upper()))

    def get_plant_modbus_config(plant_id, transport_mode=None):
        mode = transport_mode or snapshot_locked(shared_data, lambda data: data.get("transport_mode", "local"))
        endpoint = resolve_modbus_endpoint(config, plant_id, mode)
        registers = endpoint["registers"]
        return {
            "mode": mode,
            "host": endpoint.get("host", "localhost"),
            "port": int(endpoint.get("port", 5020 if plant_id == "lib" else 5021)),
            "enable_reg": registers["enable"],
            "p_setpoint_reg": registers["p_setpoint_in"],
            "q_setpoint_reg": registers["q_setpoint_in"],
            "p_battery_reg": registers["p_battery"],
            "q_battery_reg": registers["q_battery"],
        }

    def set_enable(plant_id, value):
        cfg = get_plant_modbus_config(plant_id)
        return set_enable_io(cfg, plant_id.upper(), value)

    def send_setpoints(plant_id, p_kw, q_kvar):
        cfg = get_plant_modbus_config(plant_id)
        return send_setpoints_io(cfg, plant_id.upper(), p_kw, q_kvar)

    def read_enable_state(plant_id):
        cfg = get_plant_modbus_config(plant_id)
        return read_enable_state_io(cfg)

    def wait_until_battery_power_below_threshold(plant_id, threshold_kw=1.0, timeout_s=30):
        cfg = get_plant_modbus_config(plant_id)
        return wait_until_battery_power_below_threshold_io(cfg, threshold_kw=threshold_kw, timeout_s=timeout_s)

    def safe_stop_plant(plant_id, threshold_kw=1.0, timeout_s=30):
        logging.info("Dashboard: safe-stop requested for %s.", plant_id.upper())
        with shared_data["lock"]:
            shared_data["scheduler_running_by_plant"][plant_id] = False
            shared_data["plant_transition_by_plant"][plant_id] = "stopping"
        logging.info("Dashboard: %s scheduler gate set to False.", plant_id.upper())

        zero_ok = send_setpoints(plant_id, 0.0, 0.0)
        if zero_ok:
            logging.info("Dashboard: %s zero setpoints written.", plant_id.upper())
        else:
            logging.warning("Dashboard: %s zero setpoints write failed.", plant_id.upper())

        reached = wait_until_battery_power_below_threshold(plant_id, threshold_kw=threshold_kw, timeout_s=timeout_s)
        if not reached:
            logging.warning("Dashboard: safe stop timeout for %s. Forcing disable.", plant_id.upper())
        else:
            logging.info("Dashboard: %s battery power decayed below threshold.", plant_id.upper())

        disable_ok = set_enable(plant_id, 0)
        if disable_ok:
            logging.info("Dashboard: %s disable command successful.", plant_id.upper())
        else:
            logging.error("Dashboard: %s disable command failed.", plant_id.upper())

        with shared_data["lock"]:
            shared_data["plant_transition_by_plant"][plant_id] = "stopped" if disable_ok else "unknown"

        result = {
            "threshold_reached": bool(reached),
            "disable_ok": bool(disable_ok),
        }
        logging.info(
            "Dashboard: safe-stop completed for %s (threshold_reached=%s disable_ok=%s).",
            plant_id.upper(),
            result["threshold_reached"],
            result["disable_ok"],
        )
        return result

    def safe_stop_all_plants():
        results = {}
        for plant_id in plant_ids:
            results[plant_id] = safe_stop_plant(plant_id)
        return results

    def get_latest_schedule_setpoint(plant_id):
        source_snapshot = snapshot_locked(
            shared_data,
            lambda data: {
                "source": data.get("active_schedule_source", "manual"),
                "schedule_df": (
                    data.get("api_schedule_df_by_plant", {}).get(plant_id)
                    if data.get("active_schedule_source", "manual") == "api"
                    else data.get("manual_schedule_df_by_plant", {}).get(plant_id)
                ),
            },
        )
        p_kw, q_kvar, _ = resolve_schedule_setpoint(
            source_snapshot["schedule_df"],
            now_tz(config),
            tz,
            source=source_snapshot["source"],
            api_validity_window=api_validity_window,
        )
        return p_kw, q_kvar

    def get_daily_recording_file_path(plant_id):
        safe_name = sanitize_plant_name(plant_name(plant_id), plant_id)
        date_str = now_tz(config).strftime("%Y%m%d")
        return os.path.join("data", f"{date_str}_{safe_name}.csv")

    def resolve_runtime_transition(plant_id, transition_state, enable_state):
        resolved = resolve_runtime_transition_state(transition_state, enable_state)

        if resolved != transition_state:
            with shared_data["lock"]:
                shared_data["plant_transition_by_plant"][plant_id] = resolved
        return resolved

    app.layout = build_dashboard_layout(
        config,
        plant_ids,
        plant_name,
        brand_logo_src,
        initial_transport,
        initial_source,
        now_tz(config),
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
            requested_mode = "remote" if stored_mode == "local" else "local"

            def perform_transport_switch():
                try:
                    logging.info("Dashboard: transport switch requested -> %s", requested_mode)
                    with shared_data["lock"]:
                        shared_data["transport_switching"] = True

                    safe_stop_all_plants()
                    with shared_data["lock"]:
                        for plant_id in plant_ids:
                            shared_data["scheduler_running_by_plant"][plant_id] = False
                            shared_data["plant_transition_by_plant"][plant_id] = "stopped"
                            shared_data["measurements_filename_by_plant"][plant_id] = None
                            shared_data["current_file_df_by_plant"][plant_id] = pd.DataFrame()
                            shared_data["current_file_path_by_plant"][plant_id] = None
                        shared_data["transport_mode"] = requested_mode
                        shared_data["transport_switching"] = False
                    logging.info("Dashboard: transport mode switched to %s", requested_mode)
                except Exception as exc:
                    logging.error("Dashboard: transport switch failed: %s", exc)
                    with shared_data["lock"]:
                        shared_data["transport_switching"] = False

            thread = threading.Thread(target=perform_transport_switch, daemon=True)
            thread.start()

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
            Output("active-source-selector", "data"),
            Output("source-manual-btn", "className"),
            Output("source-api-btn", "className"),
            Output("schedule-switch-modal", "className"),
        ],
        [
            Input("source-manual-btn", "n_clicks"),
            Input("source-api-btn", "n_clicks"),
            Input("schedule-switch-cancel", "n_clicks"),
            Input("schedule-switch-confirm", "n_clicks"),
        ],
        [State("active-source-selector", "data")],
        prevent_initial_call=False,
    )
    def select_source(manual_clicks, api_clicks, cancel_clicks, confirm_clicks, current_source):
        ctx = callback_context
        with shared_data["lock"]:
            stored_source = shared_data.get("active_schedule_source", "manual")
        hidden_class = "modal-overlay hidden"
        open_class = "modal-overlay"

        if current_source not in {"manual", "api"}:
            current_source = stored_source

        def classes_for(source_value):
            if source_value == "api":
                return "toggle-option", "toggle-option active"
            return "toggle-option active", "toggle-option"

        if not ctx.triggered:
            manual_class, api_class = classes_for(stored_source)
            return stored_source, manual_class, api_class, hidden_class

        trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]

        if trigger_id == "schedule-switch-cancel":
            manual_class, api_class = classes_for(stored_source)
            return stored_source, manual_class, api_class, hidden_class

        if trigger_id == "schedule-switch-confirm":
            requested_source = "api" if current_source == "manual" else "manual"

            def perform_source_switch():
                try:
                    logging.info("Dashboard: schedule source switch requested -> %s", requested_source)
                    with shared_data["lock"]:
                        shared_data["schedule_switching"] = True

                    safe_stop_all_plants()
                    with shared_data["lock"]:
                        shared_data["active_schedule_source"] = requested_source
                        shared_data["schedule_switching"] = False
                    logging.info("Dashboard: active schedule source switched to %s", requested_source)
                except Exception as exc:
                    logging.error("Dashboard: schedule source switch failed: %s", exc)
                    with shared_data["lock"]:
                        shared_data["schedule_switching"] = False

            threading.Thread(target=perform_source_switch, daemon=True).start()
            manual_class, api_class = classes_for(requested_source)
            return requested_source, manual_class, api_class, hidden_class

        if trigger_id == "source-api-btn" and current_source != "api":
            logging.info("Dashboard: source switch requested from %s to API (awaiting confirmation).", current_source.upper())
            manual_class, api_class = classes_for(current_source)
            return current_source, manual_class, api_class, open_class

        if trigger_id == "source-manual-btn" and current_source != "manual":
            logging.info("Dashboard: source switch requested from %s to MANUAL (awaiting confirmation).", current_source.upper())
            manual_class, api_class = classes_for(current_source)
            return current_source, manual_class, api_class, open_class

        manual_class, api_class = classes_for(stored_source)
        return stored_source, manual_class, api_class, hidden_class

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
        ],
        prevent_initial_call=True,
    )
    def handle_controls(*_):
        ctx = callback_context
        if not ctx.triggered:
            raise PreventUpdate

        trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]

        action_map = {
            "start-lib": ("lib", "start"),
            "stop-lib": ("lib", "stop"),
            "record-lib": ("lib", "record"),
            "record-stop-lib": ("lib", "record_stop"),
            "start-vrfb": ("vrfb", "start"),
            "stop-vrfb": ("vrfb", "stop"),
            "record-vrfb": ("vrfb", "record"),
            "record-stop-vrfb": ("vrfb", "record_stop"),
        }

        if trigger_id not in action_map:
            raise PreventUpdate

        plant_id, action = action_map[trigger_id]

        if action == "start":
            logging.info("Dashboard: start requested for %s.", plant_id.upper())
            with shared_data["lock"]:
                transition_state = shared_data["plant_transition_by_plant"].get(plant_id, "stopped")
                if transition_state in {"starting", "running"}:
                    logging.info("Dashboard: %s start ignored (state=%s).", plant_id.upper(), transition_state)
                    return f"{trigger_id}:{now_tz(config).strftime('%H%M%S')}"
                shared_data["scheduler_running_by_plant"][plant_id] = True
                shared_data["plant_transition_by_plant"][plant_id] = "starting"

            def start_sequence():
                enabled = set_enable(plant_id, 1)
                if not enabled:
                    logging.error("Dashboard: %s start failed while enabling plant.", plant_id.upper())
                    with shared_data["lock"]:
                        shared_data["scheduler_running_by_plant"][plant_id] = False
                        shared_data["plant_transition_by_plant"][plant_id] = "stopped"
                    return

                logging.info("Dashboard: %s enable command successful.", plant_id.upper())
                p_kw, q_kvar = get_latest_schedule_setpoint(plant_id)
                send_ok = send_setpoints(plant_id, p_kw, q_kvar)
                if send_ok:
                    logging.info(
                        "Dashboard: %s initial setpoints sent (P=%.3f kW Q=%.3f kvar).",
                        plant_id.upper(),
                        p_kw,
                        q_kvar,
                    )
                else:
                    logging.warning(
                        "Dashboard: %s initial setpoint write failed (P=%.3f kW Q=%.3f kvar).",
                        plant_id.upper(),
                        p_kw,
                        q_kvar,
                    )
                with shared_data["lock"]:
                    shared_data["plant_transition_by_plant"][plant_id] = "running"
                logging.info("Dashboard: %s transitioned to running.", plant_id.upper())

            threading.Thread(target=start_sequence, daemon=True).start()

        elif action == "stop":
            logging.info("Dashboard: stop requested for %s.", plant_id.upper())
            with shared_data["lock"]:
                transition_state = shared_data["plant_transition_by_plant"].get(plant_id, "stopped")
                if transition_state in {"stopping", "stopped"}:
                    logging.info("Dashboard: %s stop ignored (state=%s).", plant_id.upper(), transition_state)
                    return f"{trigger_id}:{now_tz(config).strftime('%H%M%S')}"
                shared_data["plant_transition_by_plant"][plant_id] = "stopping"

            def stop_sequence():
                result = safe_stop_plant(plant_id)
                if not result.get("disable_ok", False):
                    with shared_data["lock"]:
                        shared_data["plant_transition_by_plant"][plant_id] = "unknown"

            threading.Thread(target=stop_sequence, daemon=True).start()

        elif action == "record":
            os.makedirs("data", exist_ok=True)
            filename = get_daily_recording_file_path(plant_id)
            logging.info("Dashboard: record requested for %s -> %s", plant_id.upper(), filename)
            with shared_data["lock"]:
                shared_data["measurements_filename_by_plant"][plant_id] = filename

        elif action == "record_stop":
            logging.info("Dashboard: record stop requested for %s.", plant_id.upper())
            with shared_data["lock"]:
                shared_data["measurements_filename_by_plant"][plant_id] = None

        return f"{trigger_id}:{now_tz(config).strftime('%H%M%S')}"

    @app.callback(
        Output("manual-status-text", "children"),
        [Input("manual-generate", "n_clicks"), Input("manual-clear", "n_clicks"), Input("manual-csv-upload", "contents")],
        [
            State("manual-plant-selector", "value"),
            State("manual-start-hour", "value"),
            State("manual-end-hour", "value"),
            State("manual-step", "value"),
            State("manual-min-power", "value"),
            State("manual-max-power", "value"),
            State("manual-csv-upload", "filename"),
            State("manual-csv-date", "date"),
            State("manual-csv-hour", "value"),
            State("manual-csv-minute", "value"),
        ],
        prevent_initial_call=True,
    )
    def handle_manual_schedule(
        generate_clicks,
        clear_clicks,
        upload_contents,
        plant_id,
        start_hour,
        end_hour,
        step_minutes,
        min_power,
        max_power,
        upload_filename,
        csv_date,
        csv_hour,
        csv_minute,
    ):
        ctx = callback_context
        if not ctx.triggered:
            raise PreventUpdate

        trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]

        if plant_id not in plant_ids:
            return "Select a valid plant first."

        if trigger_id == "manual-generate":
            now_value = now_tz(config)
            start_ts = now_value.replace(hour=int(start_hour), minute=0, second=0, microsecond=0)
            end_ts = now_value.replace(hour=int(end_hour), minute=0, second=0, microsecond=0)
            if end_ts <= start_ts:
                end_ts += timedelta(days=1)

            df = msm.generate_random_schedule(
                start_time=start_ts,
                end_time=end_ts,
                step_minutes=int(step_minutes),
                min_power_kw=float(min_power),
                max_power_kw=float(max_power),
                timezone_name=config.get("TIMEZONE_NAME"),
            )
            with shared_data["lock"]:
                shared_data["manual_schedule_df_by_plant"][plant_id] = df
            return f"Random schedule generated for {plant_name(plant_id)} ({len(df)} points)."

        if trigger_id == "manual-clear":
            with shared_data["lock"]:
                shared_data["manual_schedule_df_by_plant"][plant_id] = pd.DataFrame()
            return f"Manual schedule cleared for {plant_name(plant_id)}."

        if trigger_id == "manual-csv-upload" and upload_contents:
            try:
                content_type, content_string = upload_contents.split(",")
                decoded = base64.b64decode(content_string)
                csv_text = decoded.decode("utf-8")
                df_input = pd.read_csv(io.StringIO(csv_text))

                if "datetime" not in df_input.columns or "power_setpoint_kw" not in df_input.columns:
                    return "CSV must include 'datetime' and 'power_setpoint_kw' columns."

                if "reactive_power_setpoint_kvar" not in df_input.columns:
                    df_input["reactive_power_setpoint_kvar"] = 0.0

                df_input["datetime"] = pd.to_datetime(df_input["datetime"], errors="coerce")
                df_input = df_input.dropna(subset=["datetime"]).copy()

                if csv_date:
                    start_dt = pd.Timestamp(csv_date).replace(
                        hour=int(csv_hour or 0), minute=int(csv_minute or 0), second=0, microsecond=0
                    )
                    first_dt = df_input["datetime"].iloc[0]
                    offset = start_dt - first_dt
                    df_input["datetime"] = df_input["datetime"] + offset

                schedule_df = df_input.set_index("datetime")
                schedule_df = normalize_schedule_index(schedule_df, tz)

                with shared_data["lock"]:
                    shared_data["manual_schedule_df_by_plant"][plant_id] = schedule_df

                return f"CSV '{upload_filename}' loaded for {plant_name(plant_id)} ({len(schedule_df)} points)."
            except Exception as exc:
                return f"CSV load failed: {exc}"

        raise PreventUpdate

    @app.callback(
        Output("manual-preview-graph", "figure"),
        [Input("manual-plant-selector", "value"), Input("interval-component", "n_intervals")],
    )
    def update_manual_preview(plant_id, n_intervals):
        with shared_data["lock"]:
            schedule_df = shared_data.get("manual_schedule_df_by_plant", {}).get(plant_id, pd.DataFrame()).copy()

        schedule_df = normalize_schedule_index(schedule_df, tz)
        fig = go.Figure()
        if schedule_df.empty:
            fig.add_annotation(
                text="No manual schedule for selected plant.",
                xref="paper",
                yref="paper",
                x=0.5,
                y=0.5,
                showarrow=False,
            )
        else:
            fig.add_trace(
                go.Scatter(
                    x=schedule_df.index,
                    y=schedule_df.get("power_setpoint_kw", []),
                    mode="lines",
                    line_shape="hv",
                    name=f"{plant_name(plant_id)} P Setpoint",
                    line=dict(color=trace_colors["p_setpoint"], width=2),
                )
            )
        apply_figure_theme(
            fig,
            plot_theme,
            height=320,
            margin=dict(l=40, r=20, t=40, b=30),
            uirevision=f"manual-preview:{plant_id}",
        )
        fig.update_yaxes(title_text="kW")
        return fig

    @app.callback(
        [
            Output("api-connection-status", "children"),
            Output("api-measurement-posting-status", "children"),
            Output("api-preview-graph", "figure"),
        ],
        [Input("interval-component", "n_intervals"), Input("set-password-btn", "n_clicks"), Input("disconnect-api-btn", "n_clicks")],
        [State("api-password", "value")],
    )
    def update_api_tab(n_intervals, set_clicks, disconnect_clicks, password_value):
        ctx = callback_context
        if ctx.triggered:
            trigger_id = ctx.triggered[0]["prop_id"].split(".")[0]
            if trigger_id == "set-password-btn" and password_value:
                with shared_data["lock"]:
                    shared_data["api_password"] = password_value
            elif trigger_id == "disconnect-api-btn":
                with shared_data["lock"]:
                    shared_data["api_password"] = None

        with shared_data["lock"]:
            status = shared_data.get("data_fetcher_status", {}).copy()
            api_password = shared_data.get("api_password")
            api_map = {
                plant_id: shared_data.get("api_schedule_df_by_plant", {}).get(plant_id, pd.DataFrame()).copy()
                for plant_id in plant_ids
            }
            post_status_map = {
                plant_id: dict((shared_data.get("measurement_post_status", {}) or {}).get(plant_id, {}) or {})
                for plant_id in plant_ids
            }

        connected = "Connected" if status.get("connected") else "Not connected"
        auth_state = "Password set" if api_password else "Password not set"

        points_today = status.get("today_points_by_plant", {})
        points_tomorrow = status.get("tomorrow_points_by_plant", {})
        status_text = (
            f"API: {connected} | {auth_state} | "
            f"Today {status.get('today_date')}: LIB={points_today.get('lib', 0)} VRFB={points_today.get('vrfb', 0)} | "
            f"Tomorrow {status.get('tomorrow_date')}: LIB={points_tomorrow.get('lib', 0)} VRFB={points_tomorrow.get('vrfb', 0)}"
        )
        if status.get("error"):
            status_text += f" | Error: {status.get('error')}"

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
            Output("global-status", "children"),
            Output("api-status-inline", "children"),
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
        [Input("interval-component", "n_intervals"), Input("control-action", "data")],
    )
    def update_status_and_graphs(n_intervals, control_action):
        with shared_data["lock"]:
            source = shared_data.get("active_schedule_source", "manual")
            transport_mode = shared_data.get("transport_mode", "local")
            schedule_switching = bool(shared_data.get("schedule_switching", False))
            scheduler_running = dict(shared_data.get("scheduler_running_by_plant", {}))
            transition_by_plant = dict(shared_data.get("plant_transition_by_plant", {}))
            recording_files = dict(shared_data.get("measurements_filename_by_plant", {}))
            status = shared_data.get("data_fetcher_status", {}).copy()
            if source == "api":
                schedule_map = {
                    plant_id: shared_data.get("api_schedule_df_by_plant", {}).get(plant_id, pd.DataFrame()).copy()
                    for plant_id in plant_ids
                }
            else:
                schedule_map = {
                    plant_id: shared_data.get("manual_schedule_df_by_plant", {}).get(plant_id, pd.DataFrame()).copy()
                    for plant_id in plant_ids
                }
            measurements_map = {
                plant_id: shared_data.get("current_file_df_by_plant", {}).get(plant_id, pd.DataFrame()).copy()
                for plant_id in plant_ids
            }

        enable_state_by_plant = {plant_id: read_enable_state(plant_id) for plant_id in plant_ids}
        runtime_state_by_plant = {
            plant_id: resolve_runtime_transition(
                plant_id,
                transition_by_plant.get(plant_id, "unknown"),
                enable_state_by_plant.get(plant_id),
            )
            for plant_id in plant_ids
        }

        global_text = f"Source: {source.upper()} | Transport: {transport_mode.upper()}"
        if schedule_switching:
            global_text += " | Source switching..."

        api_inline = (
            f"API Connected: {bool(status.get('connected'))} | "
            f"Today {status.get('today_date')}: LIB={status.get('today_points_by_plant', {}).get('lib', 0)} "
            f"VRFB={status.get('today_points_by_plant', {}).get('vrfb', 0)}"
        )
        if status.get("error"):
            api_inline += f" | Error: {status.get('error')}"

        def plant_status_text(plant_id):
            enable_state = enable_state_by_plant.get(plant_id)
            running = bool(scheduler_running.get(plant_id, False))
            recording = recording_files.get(plant_id)
            runtime_state = runtime_state_by_plant.get(plant_id, "unknown")
            modbus_text = "Running" if enable_state == 1 else ("Stopped" if enable_state == 0 else "Unknown")
            rec_text = f"Recording: On ({os.path.basename(recording)})" if recording else "Recording: Off"
            return (
                f"{plant_name(plant_id)} | State: {runtime_state.capitalize()} | "
                f"Scheduler gate: {running} | Modbus enable: {modbus_text} | {rec_text}"
            )

        lib_schedule = normalize_schedule_index(schedule_map.get("lib", pd.DataFrame()), tz)
        vrfb_schedule = normalize_schedule_index(schedule_map.get("vrfb", pd.DataFrame()), tz)
        lib_fig = create_plant_figure(
            "lib",
            plant_name,
            lib_schedule,
            measurements_map.get("lib", pd.DataFrame()),
            uirevision_key=f"lib:{source}:{transport_mode}",
            tz=tz,
            plot_theme=plot_theme,
            trace_colors=trace_colors,
        )
        vrfb_fig = create_plant_figure(
            "vrfb",
            plant_name,
            vrfb_schedule,
            measurements_map.get("vrfb", pd.DataFrame()),
            uirevision_key=f"vrfb:{source}:{transport_mode}",
            tz=tz,
            plot_theme=plot_theme,
            trace_colors=trace_colors,
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
            global_text,
            api_inline,
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
        app.run(debug=False, threaded=True)

    thread = threading.Thread(target=run_app, daemon=True)
    thread.start()

    while not shared_data["shutdown_event"].is_set():
        time.sleep(1)

    logging.info("Dashboard agent stopped.")


if __name__ == "__main__":
    pass
