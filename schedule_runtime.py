"""Shared runtime helpers for schedule lookup, staleness, and merging."""

import pandas as pd

from time_utils import normalize_schedule_index


def merge_schedule_frames(existing_df, new_df):
    """Merge two schedule frames, replacing overlaps with new rows."""
    if existing_df is None or existing_df.empty:
        return new_df
    if new_df is None or new_df.empty:
        return existing_df

    non_overlapping = existing_df.index.difference(new_df.index)
    return pd.concat([existing_df.loc[non_overlapping], new_df]).sort_index()


def crop_schedule_frame_to_window(schedule_df, tz, start_ts, end_ts):
    """Return a normalized schedule frame filtered to [start_ts, end_ts)."""
    normalized_df = normalize_schedule_index(schedule_df, tz)
    if normalized_df.empty:
        return normalized_df

    if start_ts is not None:
        normalized_df = normalized_df.loc[normalized_df.index >= pd.Timestamp(start_ts)]
    if end_ts is not None:
        normalized_df = normalized_df.loc[normalized_df.index < pd.Timestamp(end_ts)]
    return normalized_df


def resolve_schedule_setpoint(
    schedule_df,
    now_value,
    tz,
    *,
    source="manual",
    api_validity_window=None,
):
    """
    Resolve the runtime setpoint at `now_value`.

    Returns `(p_setpoint_kw, q_setpoint_kvar, api_is_stale_or_none)`.
    """
    if schedule_df is None or schedule_df.empty:
        return 0.0, 0.0, (True if source == "api" else None)

    normalized_df = normalize_schedule_index(schedule_df, tz)
    if normalized_df.empty:
        return 0.0, 0.0, (True if source == "api" else None)

    row = normalized_df.asof(now_value)
    if row is None:
        return 0.0, 0.0, (True if source == "api" else None)

    p_setpoint = float(row.get("power_setpoint_kw", 0.0) or 0.0)
    q_setpoint = float(row.get("reactive_power_setpoint_kvar", 0.0) or 0.0)
    if pd.isna(p_setpoint) or pd.isna(q_setpoint):
        p_setpoint = 0.0
        q_setpoint = 0.0

    api_is_stale = None
    if source == "api":
        validity_window = api_validity_window if api_validity_window is not None else pd.Timedelta(minutes=15)
        row_ts = normalized_df.index.asof(now_value)
        api_is_stale = pd.isna(row_ts) or (pd.Timestamp(now_value) - pd.Timestamp(row_ts) > validity_window)
        if api_is_stale:
            p_setpoint = 0.0
            q_setpoint = 0.0

    return p_setpoint, q_setpoint, api_is_stale
