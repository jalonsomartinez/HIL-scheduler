"""Plot/theme helpers for dashboard figures."""

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from time_utils import normalize_datetime_series


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
):
    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        subplot_titles=(
            f"{plant_name_fn(plant_id)} Active Power (kW)",
            f"{plant_name_fn(plant_id)} State of Charge (pu)",
            f"{plant_name_fn(plant_id)} Reactive Power (kvar)",
        ),
    )

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

    apply_figure_theme(
        fig,
        plot_theme,
        height=480,
        margin=dict(l=50, r=20, t=90, b=30),
        uirevision=uirevision_key,
    )
    fig.update_yaxes(title_text="kW", row=1, col=1)
    fig.update_yaxes(title_text="pu", row=2, col=1)
    fig.update_yaxes(title_text="kvar", row=3, col=1)
    fig.update_xaxes(title_text="Time", row=3, col=1)
    return fig
