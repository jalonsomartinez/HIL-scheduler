"""Plot/theme helpers for dashboard figures."""

import plotly.graph_objects as go
import pandas as pd
from plotly.subplots import make_subplots

from measurement.storage import DIGITAL_TWIN_SUMMARY_MEASUREMENT_COLUMNS
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
    "p_day_ahead": "#1d6fd0",
    "p_mfrr": "#d25c2c",
    "q_setpoint": "#8d7b00",
    "p_poi": "#006f9e",
    "p_battery": "#8fd4b2",
    "soc": "#6756d6",
    "q_poi": "#006f9e",
    "q_battery": "#b2d8c3",
    "v_poi": "#c66a00",
    "api_lib": "#00945a",
    "api_vrfb": "#3f65c8",
    "grid_map_battery_voltage": "#2f6db4",
    "grid_map_min_voltage": "#c66a00",
    "grid_map_max_voltage": "#7b59b5",
    "grid_map_max_line_loading": "#c83b3b",
    "grid_map_overloaded_lines": "#234038",
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
    day_ahead_schedule_df=None,
    mfrr_schedule_df=None,
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

    def _crop_schedule_for_plot(df):
        if df is None or df.empty:
            return None
        cropped = df
        if x_window_start is not None:
            cropped = cropped.loc[cropped.index >= x_window_start]
        if x_window_end is not None:
            cropped = cropped.loc[cropped.index < x_window_end]
        return cropped

    schedule_plot_df = _crop_schedule_for_plot(schedule_df)
    day_ahead_plot_df = _crop_schedule_for_plot(day_ahead_schedule_df)
    mfrr_plot_df = _crop_schedule_for_plot(mfrr_schedule_df)

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
    else:
        df = pd.DataFrame()

    pref_x = None
    pref_y = None
    if schedule_plot_df is not None and not schedule_plot_df.empty and "power_setpoint_kw" in schedule_plot_df.columns:
        pref_x = schedule_plot_df.index
        pref_y = schedule_plot_df["power_setpoint_kw"]
    elif not df.empty and "p_schedule_total_kw" in df.columns:
        pref_x = df["datetime"]
        pref_y = df["p_schedule_total_kw"]
    elif not df.empty and "p_setpoint_kw" in df.columns:
        pref_x = df["datetime"]
        pref_y = df["p_setpoint_kw"]

    day_ahead_x = None
    day_ahead_y = None
    if day_ahead_plot_df is not None and not day_ahead_plot_df.empty and "power_setpoint_kw" in day_ahead_plot_df.columns:
        day_ahead_x = day_ahead_plot_df.index
        day_ahead_y = day_ahead_plot_df["power_setpoint_kw"]
    elif not df.empty and "p_schedule_day_ahead_kw" in df.columns:
        day_ahead_x = df["datetime"]
        day_ahead_y = df["p_schedule_day_ahead_kw"]

    mfrr_x = None
    mfrr_y = None
    if mfrr_plot_df is not None and not mfrr_plot_df.empty and "power_setpoint_kw" in mfrr_plot_df.columns:
        mfrr_x = mfrr_plot_df.index
        mfrr_y = mfrr_plot_df["power_setpoint_kw"]
    elif not df.empty and "p_schedule_mfrr_kw" in df.columns:
        mfrr_x = df["datetime"]
        mfrr_y = df["p_schedule_mfrr_kw"]

    qref_x = None
    qref_y = None
    if schedule_plot_df is not None and not schedule_plot_df.empty and "reactive_power_setpoint_kvar" in schedule_plot_df.columns:
        qref_x = schedule_plot_df.index
        qref_y = schedule_plot_df["reactive_power_setpoint_kvar"]
    elif not df.empty and "q_setpoint_kvar" in df.columns:
        qref_x = df["datetime"]
        qref_y = df["q_setpoint_kvar"]

    legend_rank = {
        "Pref": 10,
        "day-ahead": 15,
        "mfrr": 18,
        "P POI": 20,
        "P Bat": 30,
        "SoC": 40,
        "Qref": 50,
        "Q POI": 60,
        "Q Bat": 70,
        "Voltage": 80,
    }

    # Legend order (when traces are present): Pref, day-ahead, mfrr, P POI, P Bat, SoC, Qref, Q POI, Q Bat, Voltage.
    if pref_x is not None and pref_y is not None:
        fig.add_trace(
            go.Scatter(
                x=pref_x,
                y=pref_y,
                mode="lines",
                line_shape="hv",
                name="Pref",
                line=dict(color=trace_colors["p_setpoint"], width=2, dash="dot"),
                legendrank=legend_rank["Pref"],
            ),
            row=1,
            col=1,
        )
    if day_ahead_x is not None and day_ahead_y is not None:
        fig.add_trace(
            go.Scatter(
                x=day_ahead_x,
                y=day_ahead_y,
                mode="lines",
                line_shape="hv",
                name="day-ahead",
                line=dict(color=trace_colors["p_day_ahead"], width=2, dash="dash"),
                legendrank=legend_rank["day-ahead"],
            ),
            row=1,
            col=1,
        )
    if mfrr_x is not None and mfrr_y is not None:
        fig.add_trace(
            go.Scatter(
                x=mfrr_x,
                y=mfrr_y,
                mode="lines",
                line_shape="hv",
                name="mfrr",
                line=dict(color=trace_colors["p_mfrr"], width=2, dash="longdash"),
                legendrank=legend_rank["mfrr"],
            ),
            row=1,
            col=1,
        )

    if not df.empty:
        if "battery_active_power_kw" in df.columns:
            fig.add_trace(
                go.Scatter(
                    x=df["datetime"],
                    y=df["battery_active_power_kw"],
                    mode="lines",
                    line_shape="hv",
                    name="P Bat",
                    line=dict(color=trace_colors["p_battery"], width=2),
                    legendrank=legend_rank["P Bat"],
                ),
                row=1,
                col=1,
            )
        if "p_poi_kw" in df.columns:
            fig.add_trace(
                go.Scatter(
                    x=df["datetime"],
                    y=df["p_poi_kw"],
                    mode="lines",
                    line_shape="hv",
                    name="P POI",
                    line=dict(color=trace_colors["p_poi"], width=2),
                    legendrank=legend_rank["P POI"],
                ),
                row=1,
                col=1,
            )
        if "soc_pu" in df.columns:
            fig.add_trace(
                go.Scatter(
                    x=df["datetime"],
                    y=df["soc_pu"],
                    mode="lines",
                    name="SoC",
                    line=dict(color=trace_colors["soc"], width=2),
                    legendrank=legend_rank["SoC"],
                ),
                row=2,
                col=1,
            )

        if qref_x is not None and qref_y is not None:
            fig.add_trace(
                go.Scatter(
                    x=qref_x,
                    y=qref_y,
                    mode="lines",
                    line_shape="hv",
                    name="Qref",
                    line=dict(color=trace_colors["q_setpoint"], width=2, dash="dot"),
                    legendrank=legend_rank["Qref"],
                ),
                row=3,
                col=1,
            )
        if "battery_reactive_power_kvar" in df.columns:
            fig.add_trace(
                go.Scatter(
                    x=df["datetime"],
                    y=df["battery_reactive_power_kvar"],
                    mode="lines",
                    line_shape="hv",
                    name="Q Bat",
                    line=dict(color=trace_colors["q_battery"], width=2),
                    legendrank=legend_rank["Q Bat"],
                ),
                row=3,
                col=1,
            )
        if "q_poi_kvar" in df.columns:
            fig.add_trace(
                go.Scatter(
                    x=df["datetime"],
                    y=df["q_poi_kvar"],
                    mode="lines",
                    line_shape="hv",
                    name="Q POI",
                    line=dict(color=trace_colors["q_poi"], width=2),
                    legendrank=legend_rank["Q POI"],
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
                    name="Voltage",
                    line=dict(color=trace_colors["v_poi"], width=2),
                    legendrank=legend_rank["Voltage"],
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

    elif qref_x is not None and qref_y is not None:
        # Preserve Q reference visibility when only schedule data is available.
        fig.add_trace(
            go.Scatter(
                x=qref_x,
                y=qref_y,
                mode="lines",
                line_shape="hv",
                name="Qref",
                line=dict(color=trace_colors["q_setpoint"], width=2, dash="dot"),
                legendrank=legend_rank["Qref"],
            ),
            row=3,
            col=1,
        )

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


def create_grid_map_history_figure(
    measurements_df,
    *,
    tz,
    plot_theme,
    trace_colors,
    uirevision_key="grid-map-history",
    title="Grid Map / Digital Twin",
):
    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        specs=[[{}], [{"secondary_y": True}], [{}]],
        subplot_titles=(
            "Voltage Summary (pu)",
            "Line Loading Summary",
            "Voltage Bucket Node Counts",
        ),
    )

    metric_columns = list(DIGITAL_TWIN_SUMMARY_MEASUREMENT_COLUMNS)
    if measurements_df is None or measurements_df.empty:
        df = pd.DataFrame(columns=["timestamp"] + metric_columns)
    else:
        df = measurements_df.copy()
        if "timestamp" in df.columns:
            df["datetime"] = normalize_datetime_series(df["timestamp"], tz)
            df = df.dropna(subset=["datetime"])
        else:
            df["datetime"] = []
        for column in metric_columns:
            if column not in df.columns:
                df[column] = pd.NA

    def _add_trace(column, *, name, row, color, secondary_y=False, dash=None):
        if df.empty or column not in df.columns:
            return False
        series = pd.to_numeric(df[column], errors="coerce")
        if series.dropna().empty:
            return False
        fig.add_trace(
            go.Scatter(
                x=df["datetime"],
                y=series,
                mode="lines",
                name=name,
                line=dict(color=color, width=2, dash=dash or "solid"),
            ),
            row=row,
            col=1,
            secondary_y=secondary_y,
        )
        return True

    traces_added = False
    traces_added |= _add_trace(
        "grid_map_battery_voltage_pu",
        name="Battery Voltage",
        row=1,
        color=trace_colors["grid_map_battery_voltage"],
    )
    traces_added |= _add_trace(
        "grid_map_min_voltage_pu",
        name="Lowest Voltage",
        row=1,
        color=trace_colors["grid_map_min_voltage"],
    )
    traces_added |= _add_trace(
        "grid_map_max_voltage_pu",
        name="Highest Voltage",
        row=1,
        color=trace_colors["grid_map_max_voltage"],
    )
    traces_added |= _add_trace(
        "grid_map_max_line_loading_pct",
        name="Highest Line Loading",
        row=2,
        color=trace_colors["grid_map_max_line_loading"],
    )
    traces_added |= _add_trace(
        "grid_map_num_overloaded_lines",
        name="Overloaded Lines",
        row=2,
        color=trace_colors["grid_map_overloaded_lines"],
        secondary_y=True,
        dash="dash",
    )

    bucket_specs = [
        ("grid_map_voltage_bucket_lt_0_925_count", "<0.925", "#c83b3b"),
        ("grid_map_voltage_bucket_0_925_to_0_95_count", "0.925-0.95", "#d97a1f"),
        ("grid_map_voltage_bucket_0_95_to_0_975_count", "0.95-0.975", "#d7b62a"),
        ("grid_map_voltage_bucket_0_975_to_1_025_count", "0.975-1.025", "#96cc56"),
        ("grid_map_voltage_bucket_1_025_to_1_05_count", "1.025-1.05", "#2e8f85"),
        ("grid_map_voltage_bucket_1_05_to_1_075_count", "1.05-1.075", "#5d97c9"),
        ("grid_map_voltage_bucket_gte_1_075_count", ">=1.075", "#446fbe"),
    ]
    for column, label, color in bucket_specs:
        traces_added |= _add_trace(column, name=label, row=3, color=color)

    if not traces_added:
        fig.add_annotation(
            text="No Grid Map / Digital Twin measurements found in the selected range.",
            xref="paper",
            yref="paper",
            x=0.5,
            y=0.5,
            showarrow=False,
        )

    apply_figure_theme(
        fig,
        plot_theme,
        height=720,
        margin=dict(l=50, r=50, t=90, b=30),
        uirevision=uirevision_key,
    )
    fig.update_layout(title=dict(text=title, x=0.02, xanchor="left", y=0.99))
    fig.update_xaxes(title_text="Time", row=3, col=1)
    fig.update_yaxes(title_text="pu", row=1, col=1)
    fig.update_yaxes(title_text="%", row=2, col=1, secondary_y=False)
    fig.update_yaxes(title_text="Count", row=2, col=1, secondary_y=True)
    fig.update_yaxes(title_text="Nodes", row=3, col=1)
    return fig
