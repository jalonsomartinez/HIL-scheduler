import base64
import io
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta

import dash
import pandas as pd
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, callback_context, dcc, html
from dash.exceptions import PreventUpdate
from plotly.subplots import make_subplots
from pyModbusTCP.client import ModbusClient

import manual_schedule_manager as msm
from time_utils import get_config_tz, normalize_datetime_series, normalize_schedule_index, normalize_timestamp_value, now_tz
from utils import hw_to_kw, int_to_uint16, kw_to_hw, uint16_to_int


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
    with shared_data["lock"]:
        initial_source = shared_data.get("active_schedule_source", "manual")
        initial_transport = shared_data.get("transport_mode", "local")

    brand_logo_src = app.get_asset_url("brand/Logotype i-STENTORE.png")

    plot_theme = {
        "font_family": "DM Sans, Segoe UI, Helvetica Neue, Arial, sans-serif",
        "paper_bg": "#ffffff",
        "plot_bg": "#ffffff",
        "grid": "#d7e3dd",
        "axis": "#234038",
        "text": "#1b2b26",
        "muted": "#546b63",
    }
    trace_colors = {
        "p_setpoint": "#00945a",
        "q_setpoint": "#8d7b00",
        "p_poi": "#1f7ea5",
        "p_battery": "#00c072",
        "soc": "#6756d6",
        "q_poi": "#1f7ea5",
        "q_battery": "#3d8f65",
        "api_lib": "#00945a",
        "api_vrfb": "#3f65c8",
    }

    def apply_figure_theme(fig, *, height, margin, uirevision, showlegend=True, legend_y=1.08):
        fig.update_layout(
            height=height,
            margin=margin,
            showlegend=showlegend,
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=legend_y,
                xanchor="center",
                x=0.5,
                bgcolor="rgba(255, 255, 255, 0.7)",
                bordercolor="#d7e3dd",
                borderwidth=1,
                font=dict(color=plot_theme["axis"], family=plot_theme["font_family"], size=11),
            ),
            plot_bgcolor=plot_theme["plot_bg"],
            paper_bgcolor=plot_theme["paper_bg"],
            font=dict(color=plot_theme["text"], family=plot_theme["font_family"], size=12),
            uirevision=uirevision,
        )
        fig.update_xaxes(
            gridcolor=plot_theme["grid"],
            linecolor=plot_theme["grid"],
            zerolinecolor=plot_theme["grid"],
            tickfont=dict(color=plot_theme["muted"], family=plot_theme["font_family"]),
            title_font=dict(color=plot_theme["axis"], family=plot_theme["font_family"]),
        )
        fig.update_yaxes(
            gridcolor=plot_theme["grid"],
            linecolor=plot_theme["grid"],
            zerolinecolor=plot_theme["grid"],
            tickfont=dict(color=plot_theme["muted"], family=plot_theme["font_family"]),
            title_font=dict(color=plot_theme["axis"], family=plot_theme["font_family"]),
        )
        if fig.layout.annotations:
            for annotation in fig.layout.annotations:
                annotation.font = dict(
                    color=plot_theme["axis"],
                    family=plot_theme["font_family"],
                    size=12,
                )

    def plant_name(plant_id):
        return str((plants_cfg.get(plant_id, {}) or {}).get("name", plant_id.upper()))

    def get_plant_modbus_config(plant_id, transport_mode=None):
        with shared_data["lock"]:
            mode = transport_mode or shared_data.get("transport_mode", "local")

        endpoint = ((plants_cfg.get(plant_id, {}) or {}).get("modbus", {}) or {}).get(mode, {})
        registers = endpoint.get("registers", {})
        return {
            "mode": mode,
            "host": endpoint.get("host", "localhost"),
            "port": int(endpoint.get("port", 5020 if plant_id == "lib" else 5021)),
            "enable_reg": int(registers.get("enable", 1)),
            "p_setpoint_reg": int(registers.get("p_setpoint_in", 86)),
            "q_setpoint_reg": int(registers.get("q_setpoint_in", 88)),
            "p_battery_reg": int(registers.get("p_battery", 270)),
            "q_battery_reg": int(registers.get("q_battery", 272)),
        }

    def set_enable(plant_id, value):
        cfg = get_plant_modbus_config(plant_id)
        client = ModbusClient(host=cfg["host"], port=cfg["port"])
        try:
            if not client.open():
                logging.warning("Dashboard: could not connect to %s (%s mode) for enable.", plant_id.upper(), cfg["mode"])
                return False
            return bool(client.write_single_register(cfg["enable_reg"], int(value)))
        except Exception as exc:
            logging.error("Dashboard: enable write error (%s): %s", plant_id.upper(), exc)
            return False
        finally:
            try:
                client.close()
            except Exception:
                pass

    def send_setpoints(plant_id, p_kw, q_kvar):
        cfg = get_plant_modbus_config(plant_id)
        client = ModbusClient(host=cfg["host"], port=cfg["port"])
        try:
            if not client.open():
                logging.warning("Dashboard: could not connect to %s (%s mode) for setpoints.", plant_id.upper(), cfg["mode"])
                return False
            p_ok = client.write_single_register(cfg["p_setpoint_reg"], int_to_uint16(kw_to_hw(p_kw)))
            q_ok = client.write_single_register(cfg["q_setpoint_reg"], int_to_uint16(kw_to_hw(q_kvar)))
            return bool(p_ok and q_ok)
        except Exception as exc:
            logging.error("Dashboard: setpoint write error (%s): %s", plant_id.upper(), exc)
            return False
        finally:
            try:
                client.close()
            except Exception:
                pass

    def read_enable_state(plant_id):
        cfg = get_plant_modbus_config(plant_id)
        client = ModbusClient(host=cfg["host"], port=cfg["port"])
        try:
            if not client.open():
                return None
            regs = client.read_holding_registers(cfg["enable_reg"], 1)
            if not regs:
                return None
            return int(regs[0])
        except Exception:
            return None
        finally:
            try:
                client.close()
            except Exception:
                pass

    def wait_until_battery_power_below_threshold(plant_id, threshold_kw=1.0, timeout_s=30):
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            cfg = get_plant_modbus_config(plant_id)
            client = ModbusClient(host=cfg["host"], port=cfg["port"])
            try:
                if client.open():
                    p_regs = client.read_holding_registers(cfg["p_battery_reg"], 1)
                    q_regs = client.read_holding_registers(cfg["q_battery_reg"], 1)
                    if p_regs and q_regs:
                        p_kw = hw_to_kw(uint16_to_int(p_regs[0]))
                        q_kvar = hw_to_kw(uint16_to_int(q_regs[0]))
                        if abs(p_kw) < threshold_kw and abs(q_kvar) < threshold_kw:
                            return True
            except Exception:
                pass
            finally:
                try:
                    client.close()
                except Exception:
                    pass
            time.sleep(1.0)
        return False

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
        with shared_data["lock"]:
            source = shared_data.get("active_schedule_source", "manual")
            if source == "api":
                schedule_df = shared_data.get("api_schedule_df_by_plant", {}).get(plant_id)
            else:
                schedule_df = shared_data.get("manual_schedule_df_by_plant", {}).get(plant_id)

        if schedule_df is None or schedule_df.empty:
            return 0.0, 0.0

        try:
            now_value = now_tz(config)
            schedule_df = normalize_schedule_index(schedule_df, tz)
            row = schedule_df.asof(now_value)
            if row is None or row.empty:
                return 0.0, 0.0

            if source == "api":
                row_ts = schedule_df.index.asof(now_value)
                is_stale = pd.isna(row_ts) or (pd.Timestamp(now_value) - pd.Timestamp(row_ts) > api_validity_window)
                if is_stale:
                    return 0.0, 0.0

            p_kw = row.get("power_setpoint_kw", 0.0)
            q_kvar = row.get("reactive_power_setpoint_kvar", 0.0)
            if pd.isna(p_kw) or pd.isna(q_kvar):
                return 0.0, 0.0
            return float(p_kw), float(q_kvar)
        except Exception:
            return 0.0, 0.0

    def sanitize_name_for_filename(name, fallback):
        raw = str(name).strip().lower()
        safe = "".join(ch if (ch.isalnum() or ch in "_-") else "_" for ch in raw)
        safe = safe.strip("_")
        return safe or fallback

    def get_daily_recording_file_path(plant_id):
        safe_name = sanitize_name_for_filename(plant_name(plant_id), plant_id)
        date_str = now_tz(config).strftime("%Y%m%d")
        return os.path.join("data", f"{date_str}_{safe_name}.csv")

    def resolve_runtime_transition(plant_id, transition_state, enable_state):
        if transition_state == "starting" and enable_state == 1:
            resolved = "running"
        elif transition_state == "stopping" and enable_state == 0:
            resolved = "stopped"
        elif transition_state in {"starting", "stopping", "running", "stopped"}:
            resolved = transition_state
        elif enable_state == 1:
            resolved = "running"
        elif enable_state == 0:
            resolved = "stopped"
        else:
            resolved = "unknown"

        if resolved != transition_state:
            with shared_data["lock"]:
                shared_data["plant_transition_by_plant"][plant_id] = resolved
        return resolved

    def get_plant_control_labels_and_disabled(runtime_state, recording_active):
        if runtime_state == "starting":
            start_label = "Starting..."
            start_disabled = True
            stop_label = "Stop"
            stop_disabled = True
        elif runtime_state == "running":
            start_label = "Started"
            start_disabled = True
            stop_label = "Stop"
            stop_disabled = False
        elif runtime_state == "stopping":
            start_label = "Start"
            start_disabled = True
            stop_label = "Stopping..."
            stop_disabled = True
        elif runtime_state == "stopped":
            start_label = "Start"
            start_disabled = False
            stop_label = "Stopped"
            stop_disabled = True
        else:
            start_label = "Start"
            start_disabled = False
            stop_label = "Stop"
            stop_disabled = True

        if recording_active:
            record_label = "Recording"
            record_disabled = True
            record_stop_label = "Stop Recording"
            record_stop_disabled = False
        else:
            record_label = "Record"
            record_disabled = False
            record_stop_label = "Record Stopped"
            record_stop_disabled = True

        return (
            start_label,
            start_disabled,
            stop_label,
            stop_disabled,
            record_label,
            record_disabled,
            record_stop_label,
            record_stop_disabled,
        )

    def format_log_entries(log_entries):
        formatted_entries = []
        for entry in log_entries:
            level = str(entry.get("level", "INFO")).upper()
            timestamp = str(entry.get("timestamp", ""))
            message = str(entry.get("message", ""))
            if level == "ERROR":
                color = "#ef4444"
            elif level == "WARNING":
                color = "#f97316"
            elif level == "INFO":
                color = "#22c55e"
            else:
                color = "#94a3b8"

            formatted_entries.append(
                html.Div(
                    [
                        html.Span(f"[{timestamp}] ", style={"color": "#94a3b8"}),
                        html.Span(f"{level}: ", style={"color": color, "fontWeight": "600"}),
                        html.Span(message, style={"color": "#e2e8f0"}),
                    ]
                )
            )
        return formatted_entries

    def parse_and_format_historical_logs(file_content):
        pattern = r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) - (\w+) - (.+)"
        formatted_entries = []
        for line in (file_content or "").splitlines():
            match = re.match(pattern, line.strip())
            if not match:
                continue

            timestamp, level, message = match.groups()
            level = level.upper()
            if level == "ERROR":
                color = "#ef4444"
            elif level == "WARNING":
                color = "#f97316"
            elif level == "INFO":
                color = "#22c55e"
            else:
                color = "#94a3b8"

            formatted_entries.append(
                html.Div(
                    [
                        html.Span(f"[{timestamp}] ", style={"color": "#94a3b8"}),
                        html.Span(f"{level}: ", style={"color": color, "fontWeight": "600"}),
                        html.Span(message, style={"color": "#e2e8f0"}),
                    ]
                )
            )
        return formatted_entries

    def create_plant_figure(plant_id, schedule_df, measurements_df, uirevision_key):
        fig = make_subplots(
            rows=3,
            cols=1,
            shared_xaxes=True,
            vertical_spacing=0.08,
            subplot_titles=(
                f"{plant_name(plant_id)} Active Power (kW)",
                f"{plant_name(plant_id)} State of Charge (pu)",
                f"{plant_name(plant_id)} Reactive Power (kvar)",
            ),
        )

        if schedule_df is not None and not schedule_df.empty:
            fig.add_trace(
                go.Scatter(
                    x=schedule_df.index,
                    y=schedule_df.get("power_setpoint_kw", []),
                    mode="lines",
                    line_shape="hv",
                    name=f"{plant_name(plant_id)} P Setpoint",
                    line=dict(color=trace_colors["p_setpoint"], width=2),
                ),
                row=1,
                col=1,
            )

            if "reactive_power_setpoint_kvar" in schedule_df.columns:
                fig.add_trace(
                    go.Scatter(
                        x=schedule_df.index,
                        y=schedule_df["reactive_power_setpoint_kvar"],
                        mode="lines",
                        line_shape="hv",
                        name=f"{plant_name(plant_id)} Q Setpoint",
                        line=dict(color=trace_colors["q_setpoint"], width=2),
                    ),
                    row=3,
                    col=1,
                )

        if measurements_df is not None and not measurements_df.empty:
            df = measurements_df.copy()
            if "timestamp" in df.columns:
                df["datetime"] = normalize_datetime_series(df["timestamp"], tz)
                df = df.dropna(subset=["datetime"])
            else:
                df["datetime"] = []

            if not df.empty:
                fig.add_trace(
                    go.Scatter(
                        x=df["datetime"],
                        y=df["p_poi_kw"],
                        mode="lines",
                        line_shape="hv",
                        name=f"{plant_name(plant_id)} P POI",
                        line=dict(color=trace_colors["p_poi"], width=2, dash="dot"),
                    ),
                    row=1,
                    col=1,
                )
                fig.add_trace(
                    go.Scatter(
                        x=df["datetime"],
                        y=df["battery_active_power_kw"],
                        mode="lines",
                        line_shape="hv",
                        name=f"{plant_name(plant_id)} P Battery",
                        line=dict(color=trace_colors["p_battery"], width=2),
                    ),
                    row=1,
                    col=1,
                )
                fig.add_trace(
                    go.Scatter(
                        x=df["datetime"],
                        y=df["soc_pu"],
                        mode="lines",
                        name=f"{plant_name(plant_id)} SoC",
                        line=dict(color=trace_colors["soc"], width=2),
                    ),
                    row=2,
                    col=1,
                )
                fig.add_trace(
                    go.Scatter(
                        x=df["datetime"],
                        y=df["q_poi_kvar"],
                        mode="lines",
                        line_shape="hv",
                        name=f"{plant_name(plant_id)} Q POI",
                        line=dict(color=trace_colors["q_poi"], width=2, dash="dot"),
                    ),
                    row=3,
                    col=1,
                )
                fig.add_trace(
                    go.Scatter(
                        x=df["datetime"],
                        y=df["battery_reactive_power_kvar"],
                        mode="lines",
                        line_shape="hv",
                        name=f"{plant_name(plant_id)} Q Battery",
                        line=dict(color=trace_colors["q_battery"], width=2),
                    ),
                    row=3,
                    col=1,
                )

        apply_figure_theme(
            fig,
            height=480,
            margin=dict(l=50, r=20, t=90, b=30),
            uirevision=uirevision_key,
        )
        fig.update_yaxes(title_text="kW", row=1, col=1)
        fig.update_yaxes(title_text="pu", row=2, col=1)
        fig.update_yaxes(title_text="kvar", row=3, col=1)
        fig.update_xaxes(title_text="Time", row=3, col=1)
        return fig

    app.layout = html.Div(
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
                                    html.P("Dispatch, recording, and API observability for LIB and VRFB plants.", className="app-subtitle"),
                                ],
                            ),
                        ],
                    )
                ],
            ),
            dcc.Tabs(
                id="main-tabs",
                value="status",
                className="main-tabs",
                parent_className="main-tabs-parent",
                children=[
                    dcc.Tab(
                        label="Status & Plots",
                        value="status",
                        className="main-tab",
                        selected_className="main-tab--selected",
                        children=[
                            html.Div(
                                className="control-panel",
                                children=[
                                    html.Div(
                                        className="controls-row",
                                        children=[
                                            html.Div(
                                                className="control-section",
                                                children=[
                                                    html.Span("Schedule Source", className="toggle-label"),
                                                    html.Div(
                                                        className="compact-toggle",
                                                        children=[
                                                            html.Button("Manual", id="source-manual-btn", className="toggle-option active", n_clicks=0),
                                                            html.Button("API", id="source-api-btn", className="toggle-option", n_clicks=0),
                                                        ],
                                                    ),
                                                ],
                                            ),
                                            html.Div(
                                                className="control-section",
                                                children=[
                                                    html.Span("Transport Mode", className="toggle-label"),
                                                    html.Div(
                                                        className="compact-toggle",
                                                        children=[
                                                            html.Button("Local", id="transport-local-btn", className="toggle-option active", n_clicks=0),
                                                            html.Button("Remote", id="transport-remote-btn", className="toggle-option", n_clicks=0),
                                                        ],
                                                    ),
                                                ],
                                            ),
                                            html.Div(className="control-section", children=[html.Div(id="global-status", className="status-text")]),
                                        ],
                                    ),
                                    html.Div(id="api-status-inline", className="status-text"),
                                ],
                            ),
                            html.Div(
                                id="schedule-switch-modal",
                                className="modal-overlay hidden",
                                children=[
                                    html.Div(
                                        className="modal-card",
                                        children=[
                                            html.H3("Confirm Schedule Source Switch", className="modal-title"),
                                            html.P("Switching schedule source will safe-stop both plants. Recording will remain active. Continue?"),
                                            html.Div(
                                                className="modal-actions",
                                                children=[
                                                    html.Button("Cancel", id="schedule-switch-cancel", className="btn btn-secondary"),
                                                    html.Button("Confirm", id="schedule-switch-confirm", className="btn btn-primary"),
                                                ],
                                            ),
                                        ],
                                    )
                                ],
                            ),
                            html.Div(
                                id="transport-switch-modal",
                                className="modal-overlay hidden",
                                children=[
                                    html.Div(
                                        className="modal-card",
                                        children=[
                                            html.H3("Confirm Transport Switch", className="modal-title"),
                                            html.P("Switching transport mode will safe-stop both plants, stop recording, and clear plot caches. Continue?"),
                                            html.Div(
                                                className="modal-actions",
                                                children=[
                                                    html.Button("Cancel", id="transport-switch-cancel", className="btn btn-secondary"),
                                                    html.Button("Confirm", id="transport-switch-confirm", className="btn btn-primary"),
                                                ],
                                            ),
                                        ],
                                    )
                                ],
                            ),
                            html.Div(
                                className="plant-card",
                                children=[
                                    html.H3(f"{plant_name('lib')}"),
                                    html.Div(
                                        className="plant-controls-row",
                                        children=[
                                            html.Div(
                                                className="control-group plant-control-group",
                                                children=[
                                                    html.Button("Start", id="start-lib", className="control-btn control-btn-start", n_clicks=0, disabled=False),
                                                    html.Button("Stopped", id="stop-lib", className="control-btn control-btn-stop", n_clicks=0, disabled=True),
                                                ],
                                            ),
                                            html.Div(className="control-separator"),
                                            html.Div(
                                                className="control-group record-control-group",
                                                children=[
                                                    html.Button("Record", id="record-lib", className="control-btn control-btn-record", n_clicks=0, disabled=False),
                                                    html.Button("Record Stopped", id="record-stop-lib", className="control-btn control-btn-record-stop", n_clicks=0, disabled=True),
                                                ],
                                            ),
                                        ],
                                    ),
                                    html.Div(id="status-lib", className="status-text"),
                                    dcc.Graph(id="graph-lib", className="plot-graph"),
                                ],
                            ),
                            html.Div(
                                className="plant-card",
                                children=[
                                    html.H3(f"{plant_name('vrfb')}"),
                                    html.Div(
                                        className="plant-controls-row",
                                        children=[
                                            html.Div(
                                                className="control-group plant-control-group",
                                                children=[
                                                    html.Button("Start", id="start-vrfb", className="control-btn control-btn-start", n_clicks=0, disabled=False),
                                                    html.Button("Stopped", id="stop-vrfb", className="control-btn control-btn-stop", n_clicks=0, disabled=True),
                                                ],
                                            ),
                                            html.Div(className="control-separator"),
                                            html.Div(
                                                className="control-group record-control-group",
                                                children=[
                                                    html.Button("Record", id="record-vrfb", className="control-btn control-btn-record", n_clicks=0, disabled=False),
                                                    html.Button("Record Stopped", id="record-stop-vrfb", className="control-btn control-btn-record-stop", n_clicks=0, disabled=True),
                                                ],
                                            ),
                                        ],
                                    ),
                                    html.Div(id="status-vrfb", className="status-text"),
                                    dcc.Graph(id="graph-vrfb", className="plot-graph"),
                                ],
                            ),
                        ],
                    ),
                    dcc.Tab(
                        label="Manual Schedule",
                        value="manual",
                        className="main-tab",
                        selected_className="main-tab--selected",
                        children=[
                            html.Div(
                                className="card",
                                children=[
                                    html.Div(
                                        className="form-row",
                                        children=[
                                            html.Div(
                                                className="form-group",
                                                children=[
                                                    html.Label("Plant"),
                                                    dcc.Dropdown(
                                                        id="manual-plant-selector",
                                                        options=[{"label": plant_name(pid), "value": pid} for pid in plant_ids],
                                                        value="lib",
                                                        clearable=False,
                                                    ),
                                                ],
                                            ),
                                            html.Div(
                                                className="form-group",
                                                children=[
                                                    html.Label("Start Hour"),
                                                    dcc.Dropdown(
                                                        id="manual-start-hour",
                                                        options=[{"label": f"{h:02d}", "value": h} for h in range(24)],
                                                        value=now_tz(config).hour,
                                                        clearable=False,
                                                    ),
                                                ],
                                            ),
                                            html.Div(
                                                className="form-group",
                                                children=[
                                                    html.Label("End Hour"),
                                                    dcc.Dropdown(
                                                        id="manual-end-hour",
                                                        options=[{"label": f"{h:02d}", "value": h} for h in range(24)],
                                                        value=(now_tz(config).hour + 1) % 24,
                                                        clearable=False,
                                                    ),
                                                ],
                                            ),
                                            html.Div(
                                                className="form-group",
                                                children=[
                                                    html.Label("Step (min)"),
                                                    dcc.Dropdown(
                                                        id="manual-step",
                                                        options=[{"label": str(v), "value": v} for v in [5, 10, 15, 30, 60]],
                                                        value=5,
                                                        clearable=False,
                                                    ),
                                                ],
                                            ),
                                            html.Div(className="form-group", children=[html.Label("Min kW"), dcc.Input(id="manual-min-power", className="form-control", type="number", value=-1000)]),
                                            html.Div(className="form-group", children=[html.Label("Max kW"), dcc.Input(id="manual-max-power", className="form-control", type="number", value=1000)]),
                                        ],
                                    ),
                                    html.Div(
                                        className="form-row",
                                        children=[
                                            html.Button("Generate Random", id="manual-generate", className="btn btn-primary", n_clicks=0),
                                            html.Button("Clear Plant Schedule", id="manual-clear", className="btn btn-danger", n_clicks=0),
                                        ],
                                    ),
                                    html.Div(
                                        className="form-row",
                                        children=[
                                            html.Div(
                                                className="form-group",
                                                children=[
                                                    html.Label("CSV Upload"),
                                                    dcc.Upload(
                                                        id="manual-csv-upload",
                                                        className="file-upload",
                                                        children=html.Div(["Drag/drop or ", html.A("select CSV")]),
                                                        multiple=False,
                                                    ),
                                                ],
                                            ),
                                            html.Div(
                                                className="form-group",
                                                children=[html.Label("CSV Start Date"), dcc.DatePickerSingle(id="manual-csv-date", date=now_tz(config).date(), className="date-picker")],
                                            ),
                                            html.Div(
                                                className="form-group",
                                                children=[
                                                    html.Label("CSV Start Hour"),
                                                    dcc.Dropdown(
                                                        id="manual-csv-hour",
                                                        options=[{"label": f"{h:02d}", "value": h} for h in range(24)],
                                                        value=now_tz(config).hour,
                                                        clearable=False,
                                                    ),
                                                ],
                                            ),
                                            html.Div(
                                                className="form-group",
                                                children=[
                                                    html.Label("CSV Start Min"),
                                                    dcc.Dropdown(
                                                        id="manual-csv-minute",
                                                        options=[{"label": f"{m:02d}", "value": m} for m in range(0, 60, 5)],
                                                        value=0,
                                                        clearable=False,
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                    html.Div(id="manual-status-text", className="status-text"),
                                    dcc.Graph(id="manual-preview-graph", className="plot-graph"),
                                ],
                            )
                        ],
                    ),
                    dcc.Tab(
                        label="API Schedule",
                        value="api",
                        className="main-tab",
                        selected_className="main-tab--selected",
                        children=[
                            html.Div(
                                className="card",
                                children=[
                                    html.Div(
                                        className="form-row",
                                        children=[
                                            dcc.Input(id="api-password", type="password", placeholder="API password", className="form-control"),
                                            html.Button("Set Password", id="set-password-btn", className="btn btn-primary", n_clicks=0),
                                            html.Button("Disconnect", id="disconnect-api-btn", className="btn btn-danger", n_clicks=0),
                                        ],
                                    ),
                                    html.Div(id="api-connection-status", className="status-text"),
                                    html.Div(id="api-measurement-posting-status"),
                                    dcc.Graph(id="api-preview-graph", className="plot-graph"),
                                ],
                            )
                        ],
                    ),
                    dcc.Tab(
                        label="Logs",
                        value="logs",
                        className="main-tab",
                        selected_className="main-tab--selected",
                        children=[
                            html.Div(
                                className="card",
                                children=[
                                    html.Div(
                                        className="card-header logs-header",
                                        children=[
                                            html.H3(className="card-title", children="Session Logs"),
                                            html.Div(
                                                className="logs-header-actions",
                                                children=[
                                                    html.Div(id="log-file-path", className="log-file-path"),
                                                    dcc.Dropdown(
                                                        id="log-file-selector",
                                                        className="log-selector-dropdown",
                                                        options=[],
                                                        value="current_session",
                                                        clearable=False,
                                                    ),
                                                ],
                                            ),
                                        ],
                                    ),
                                    html.Div(id="logs-display", className="logs-display"),
                                ],
                            )
                        ],
                    ),
                ],
            ),
            dcc.Store(id="control-action", data="idle"),
            dcc.Store(id="transport-mode-selector", data=initial_transport),
            dcc.Store(id="active-source-selector", data=initial_source),
            dcc.Interval(id="interval-component", interval=int(float(config.get("MEASUREMENT_PERIOD_S", 1)) * 1000), n_intervals=0),
        ],
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
            lib_schedule,
            measurements_map.get("lib", pd.DataFrame()),
            uirevision_key=f"lib:{source}:{transport_mode}",
        )
        vrfb_fig = create_plant_figure(
            "vrfb",
            vrfb_schedule,
            measurements_map.get("vrfb", pd.DataFrame()),
            uirevision_key=f"vrfb:{source}:{transport_mode}",
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
        options = [{"label": "Current Session", "value": "current_session"}]
        try:
            if os.path.exists("logs"):
                log_files = []
                for filename in os.listdir("logs"):
                    if not filename.endswith(".log"):
                        continue
                    try:
                        date_str = filename.split("_", 1)[0]
                        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
                        log_files.append((date_obj.strftime("%Y-%m-%d"), filename))
                    except (ValueError, IndexError):
                        log_files.append((filename, filename))

                log_files.sort(key=lambda item: item[0], reverse=True)
                for display_name, filename in log_files:
                    options.append({"label": display_name, "value": os.path.join("logs", filename)})
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
        selected = selected_file or "current_session"

        if trigger_id == "interval-component" and selected != "current_session":
            raise PreventUpdate

        if selected == "current_session":
            with shared_data["log_lock"]:
                session_logs = list(shared_data.get("session_logs", []))
                log_file_path = shared_data.get("log_file_path", "")
            formatted = format_log_entries(session_logs)
            if not formatted:
                formatted = [html.Div("No logs yet.", className="logs-empty")]
            path_text = f"Log file: {log_file_path}" if log_file_path else "Current Session"
            return formatted, path_text

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
