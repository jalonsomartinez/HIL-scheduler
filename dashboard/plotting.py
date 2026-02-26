"""Plot/theme helpers for dashboard figures."""

import plotly.graph_objects as go
import pandas as pd
from plotly.subplots import make_subplots

from time_utils import normalize_datetime_series, normalize_schedule_index


DEFAULT_PLOT_THEME = {
    "font_family": "DM Sans, Segoe UI, Helvetica Neue, Arial, sans-serif",
    "paper_bg": "#ffffff",
    "plot_bg": "#ffffff",
    "grid": "#d7e3dd",
    "axis": "#234038",
    "text": "#1b2b26",
    "muted": "#546b63",
}

DEFAULT_TRACE_COLORS = {
    "p_setpoint": "#00945a",
    "q_setpoint": "#8d7b00",
    "p_poi": "#1f7ea5",
    "p_battery": "#00c072",
    "soc": "#6756d6",
    "q_poi": "#1f7ea5",
    "q_battery": "#3d8f65",
    "v_poi": "#c66a00",
    "api_lib": "#00945a",
    "api_vrfb": "#3f65c8",
}


def apply_figure_theme(fig, plot_theme, *, height, margin, uirevision, showlegend=True, legend_y=1.08):
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


def create_plant_figure(
    plant_id,
    plant_name_fn,
    schedule_df,
    measurements_df,
    uirevision_key,
    tz,
    plot_theme,
    trace_colors,
    x_window_start=None,
    x_window_end=None,
    time_indicator_ts=None,
    voltage_autorange_padding_kv=None,
):
    fig = make_subplots(
        rows=4,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.06,
        subplot_titles=(
            f"{plant_name_fn(plant_id)} Active Power (kW)",
            f"{plant_name_fn(plant_id)} State of Charge (pu)",
            f"{plant_name_fn(plant_id)} Reactive Power (kvar)",
            f"{plant_name_fn(plant_id)} Voltage (kV)",
        ),
    )

    p_setpoint_added = False
    q_setpoint_added = False

    if schedule_df is not None and not schedule_df.empty:
        schedule_plot_df = schedule_df
        if x_window_start is not None:
            schedule_plot_df = schedule_plot_df.loc[schedule_plot_df.index >= x_window_start]
        if x_window_end is not None:
            schedule_plot_df = schedule_plot_df.loc[schedule_plot_df.index < x_window_end]
    else:
        schedule_plot_df = None

    if schedule_plot_df is not None and not schedule_plot_df.empty:
        fig.add_trace(
            go.Scatter(
                x=schedule_plot_df.index,
                y=schedule_plot_df.get("power_setpoint_kw", []),
                mode="lines",
                line_shape="hv",
                name=f"{plant_name_fn(plant_id)} P Setpoint",
                line=dict(color=trace_colors["p_setpoint"], width=2),
            ),
            row=1,
            col=1,
        )
        p_setpoint_added = True

        if "reactive_power_setpoint_kvar" in schedule_plot_df.columns:
            fig.add_trace(
                go.Scatter(
                    x=schedule_plot_df.index,
                    y=schedule_plot_df["reactive_power_setpoint_kvar"],
                    mode="lines",
                    line_shape="hv",
                    name=f"{plant_name_fn(plant_id)} Q Setpoint",
                    line=dict(color=trace_colors["q_setpoint"], width=2),
                ),
                row=3,
                col=1,
            )
            q_setpoint_added = True

    if measurements_df is not None and not measurements_df.empty:
        df = measurements_df.copy()
        if "timestamp" in df.columns:
            df["datetime"] = normalize_datetime_series(df["timestamp"], tz)
            df = df.dropna(subset=["datetime"])
        else:
            df["datetime"] = []

        if x_window_start is not None:
            df = df.loc[df["datetime"] >= x_window_start]
        if x_window_end is not None:
            df = df.loc[df["datetime"] < x_window_end]

        if not df.empty:
            if not p_setpoint_added and "p_setpoint_kw" in df.columns:
                fig.add_trace(
                    go.Scatter(
                        x=df["datetime"],
                        y=df["p_setpoint_kw"],
                        mode="lines",
                        line_shape="hv",
                        name=f"{plant_name_fn(plant_id)} P Setpoint",
                        line=dict(color=trace_colors["p_setpoint"], width=2),
                    ),
                    row=1,
                    col=1,
                )
                p_setpoint_added = True
            if not q_setpoint_added and "q_setpoint_kvar" in df.columns:
                fig.add_trace(
                    go.Scatter(
                        x=df["datetime"],
                        y=df["q_setpoint_kvar"],
                        mode="lines",
                        line_shape="hv",
                        name=f"{plant_name_fn(plant_id)} Q Setpoint",
                        line=dict(color=trace_colors["q_setpoint"], width=2),
                    ),
                    row=3,
                    col=1,
                )
                q_setpoint_added = True
            fig.add_trace(
                go.Scatter(
                    x=df["datetime"],
                    y=df["p_poi_kw"],
                    mode="lines",
                    line_shape="hv",
                    name=f"{plant_name_fn(plant_id)} P POI",
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
                    name=f"{plant_name_fn(plant_id)} P Battery",
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
                    name=f"{plant_name_fn(plant_id)} SoC",
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
                    name=f"{plant_name_fn(plant_id)} Q POI",
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
                    name=f"{plant_name_fn(plant_id)} Q Battery",
                    line=dict(color=trace_colors["q_battery"], width=2),
                ),
                row=3,
                col=1,
            )
            if "v_poi_kV" in df.columns:
                voltage_series = pd.to_numeric(df["v_poi_kV"], errors="coerce")
                fig.add_trace(
                    go.Scatter(
                        x=df["datetime"],
                        y=df["v_poi_kV"],
                        mode="lines",
                        name=f"{plant_name_fn(plant_id)} Voltage",
                        line=dict(color=trace_colors["v_poi"], width=2),
                    ),
                    row=4,
                    col=1,
                )
                try:
                    voltage_padding = float(voltage_autorange_padding_kv)
                except (TypeError, ValueError):
                    voltage_padding = None
                if voltage_padding is not None and voltage_padding > 0.0:
                    v_min = voltage_series.min(skipna=True)
                    v_max = voltage_series.max(skipna=True)
                    try:
                        fig.update_yaxes(
                            range=[float(v_min) - voltage_padding, float(v_max) + voltage_padding],
                            row=4,
                            col=1,
                        )
                    except Exception:
                        pass

    apply_figure_theme(
        fig,
        plot_theme,
        height=640,
        margin=dict(l=50, r=20, t=90, b=30),
        uirevision=uirevision_key,
    )
    if time_indicator_ts is not None:
        for row in (1, 2, 3, 4):
            fig.add_vline(
                x=time_indicator_ts,
                row=row,
                col=1,
                line_dash="dash",
                line_width=1,
                line_color=plot_theme["muted"],
                opacity=0.8,
            )
    fig.update_yaxes(title_text="kW", row=1, col=1)
    fig.update_yaxes(title_text="pu", row=2, col=1)
    fig.update_yaxes(title_text="kvar", row=3, col=1)
    fig.update_yaxes(title_text="kV", row=4, col=1)
    fig.update_xaxes(title_text="Time", row=4, col=1)
    return fig


def create_manual_series_figure(
    *,
    title,
    unit_label,
    staged_series_df,
    applied_series_df=None,
    applied_enabled=False,
    tz,
    plot_theme,
    line_color,
    x_window_start=None,
    x_window_end=None,
    uirevision_key="manual-series",
):
    fig = go.Figure()
    staged_df = normalize_schedule_index(staged_series_df, tz) if staged_series_df is not None else None
    applied_df = normalize_schedule_index(applied_series_df, tz) if applied_series_df is not None else None
    if staged_df is not None and not staged_df.empty:
        if x_window_start is not None:
            staged_df = staged_df.loc[staged_df.index >= x_window_start]
        if x_window_end is not None:
            staged_df = staged_df.loc[staged_df.index < x_window_end]
    if applied_df is not None and not applied_df.empty:
        if x_window_start is not None:
            applied_df = applied_df.loc[applied_df.index >= x_window_start]
        if x_window_end is not None:
            applied_df = applied_df.loc[applied_df.index < x_window_end]

    staged_ok = staged_df is not None and not staged_df.empty and "setpoint" in staged_df.columns
    applied_ok = applied_df is not None and not applied_df.empty and "setpoint" in applied_df.columns

    if not staged_ok and not applied_ok:
        fig.add_annotation(text="No manual schedule.", xref="paper", yref="paper", x=0.5, y=0.5, showarrow=False)
    if staged_ok:
        fig.add_trace(
            go.Scatter(
                x=staged_df.index,
                y=staged_df["setpoint"],
                mode="lines",
                line_shape="hv",
                name="Staged (Editor)",
                line=dict(
                    color=line_color,
                    width=2,
                    dash="solid",
                ),
            )
        )
    if applied_ok:
        fig.add_trace(
            go.Scatter(
                x=applied_df.index,
                y=applied_df["setpoint"],
                mode="lines",
                line_shape="hv",
                name="Applied (Server)",
                line=dict(
                    color=line_color if applied_enabled else plot_theme["muted"],
                    width=2,
                    dash="dash",
                ),
            )
        )
        if not applied_enabled:
            fig.add_annotation(
                text="Applied schedule inactive (not merged)",
                xref="paper",
                yref="paper",
                x=0.99,
                y=0.98,
                xanchor="right",
                yanchor="top",
                showarrow=False,
                font=dict(color=plot_theme["muted"], size=11, family=plot_theme["font_family"]),
            )
    elif staged_ok:
        fig.add_annotation(
            text="No schedule sent to server yet",
            xref="paper",
            yref="paper",
            x=0.99,
            y=0.98,
            xanchor="right",
            yanchor="top",
            showarrow=False,
            font=dict(color=plot_theme["muted"], size=11, family=plot_theme["font_family"]),
        )

    apply_figure_theme(
        fig,
        plot_theme,
        height=260,
        margin=dict(l=45, r=20, t=45, b=28),
        uirevision=uirevision_key,
    )
    fig.update_layout(title=dict(text=title, x=0.02, xanchor="left", y=0.96))
    fig.update_yaxes(title_text=unit_label)
    fig.update_xaxes(title_text="Time")
    return fig
