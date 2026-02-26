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


def resolve_series_setpoint_asof(series_df, now_value, tz):
    """Resolve a single-column manual override series as-of value."""
    if series_df is None or series_df.empty:
        return 0.0, False

    normalized_df = normalize_schedule_index(series_df, tz)
    if normalized_df.empty:
        return 0.0, False

    if "setpoint" not in normalized_df.columns:
        return 0.0, False

    row = normalized_df.asof(now_value)
    row_ts = normalized_df.index.asof(now_value)
    if row is None or pd.isna(row_ts):
        return 0.0, False

    try:
        value = float(row.get("setpoint", 0.0))
    except (TypeError, ValueError):
        return 0.0, False
    if pd.isna(value):
        return 0.0, False
    return value, True


def split_manual_override_series(series_df, tz):
    """
    Normalize a manual series and detect the terminal duplicate-row end marker.

    Returns dict:
      - series_df: normalized numeric dataframe (full stored series)
      - end_ts: terminal timestamp if terminal duplicate marker is present, else None
      - has_terminal_end: bool
    """
    normalized_df = normalize_schedule_index(series_df, tz)
    if normalized_df.empty or "setpoint" not in normalized_df.columns:
        return {"series_df": pd.DataFrame(columns=["setpoint"]), "end_ts": None, "has_terminal_end": False}

    df = normalized_df[["setpoint"]].copy()
    df["setpoint"] = pd.to_numeric(df["setpoint"], errors="coerce")
    df = df.dropna(subset=["setpoint"]).sort_index()
    if df.empty:
        return {"series_df": pd.DataFrame(columns=["setpoint"]), "end_ts": None, "has_terminal_end": False}

    has_terminal_end = False
    end_ts = None
    if len(df) >= 2:
        prev_row = df.iloc[-2]
        last_row = df.iloc[-1]
        prev_ts = pd.Timestamp(df.index[-2])
        last_ts = pd.Timestamp(df.index[-1])
        try:
            prev_value = float(prev_row.get("setpoint"))
            last_value = float(last_row.get("setpoint"))
        except (TypeError, ValueError):
            prev_value = None
            last_value = None
        if (
            prev_value is not None
            and last_value is not None
            and not pd.isna(prev_value)
            and not pd.isna(last_value)
            and last_ts > prev_ts
            and last_value == prev_value
        ):
            has_terminal_end = True
            end_ts = last_ts

    return {"series_df": df, "end_ts": end_ts, "has_terminal_end": has_terminal_end}


def _ffill_column_on_union(df, union_index, column_name):
    if df is None or df.empty or column_name not in df.columns:
        return pd.Series(index=union_index, dtype=float)
    series = pd.to_numeric(df[column_name], errors="coerce")
    return series.reindex(union_index).ffill()


def build_effective_schedule_frame(
    api_df,
    manual_p_df,
    manual_q_df,
    *,
    manual_p_enabled,
    manual_q_enabled,
    tz,
):
    """
    Build an effective per-plant schedule frame from API base plus manual per-signal overrides.

    Output columns: `power_setpoint_kw`, `reactive_power_setpoint_kvar`.
    """
    api_norm = normalize_schedule_index(api_df, tz)
    p_parts = split_manual_override_series(manual_p_df, tz)
    q_parts = split_manual_override_series(manual_q_df, tz)
    p_norm = p_parts["series_df"]
    q_norm = q_parts["series_df"]
    p_end_ts = p_parts["end_ts"]
    q_end_ts = q_parts["end_ts"]

    union_index = pd.DatetimeIndex([])
    for df in (api_norm, p_norm, q_norm):
        if df is not None and not df.empty:
            union_index = union_index.union(df.index)
    if p_end_ts is not None:
        union_index = union_index.union(pd.DatetimeIndex([pd.Timestamp(p_end_ts)]))
    if q_end_ts is not None:
        union_index = union_index.union(pd.DatetimeIndex([pd.Timestamp(q_end_ts)]))
    union_index = union_index.sort_values()
    if len(union_index) == 0:
        return pd.DataFrame()

    effective = pd.DataFrame(index=union_index)
    effective["power_setpoint_kw"] = _ffill_column_on_union(api_norm, union_index, "power_setpoint_kw")
    effective["reactive_power_setpoint_kvar"] = _ffill_column_on_union(api_norm, union_index, "reactive_power_setpoint_kvar")

    if manual_p_enabled and p_norm is not None and not p_norm.empty and "setpoint" in p_norm.columns:
        p_override = pd.to_numeric(p_norm["setpoint"], errors="coerce").reindex(union_index).ffill()
        p_mask = p_override.notna()
        if p_end_ts is not None:
            p_mask = p_mask & (effective.index < pd.Timestamp(p_end_ts))
        effective.loc[p_mask, "power_setpoint_kw"] = p_override[p_mask]

    if manual_q_enabled and q_norm is not None and not q_norm.empty and "setpoint" in q_norm.columns:
        q_override = pd.to_numeric(q_norm["setpoint"], errors="coerce").reindex(union_index).ffill()
        q_mask = q_override.notna()
        if q_end_ts is not None:
            q_mask = q_mask & (effective.index < pd.Timestamp(q_end_ts))
        effective.loc[q_mask, "reactive_power_setpoint_kvar"] = q_override[q_mask]

    effective["power_setpoint_kw"] = pd.to_numeric(effective["power_setpoint_kw"], errors="coerce").fillna(0.0)
    effective["reactive_power_setpoint_kvar"] = (
        pd.to_numeric(effective["reactive_power_setpoint_kvar"], errors="coerce").fillna(0.0)
    )
    return effective.sort_index()


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
