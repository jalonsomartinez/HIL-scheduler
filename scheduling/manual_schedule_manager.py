"""
Manual Schedule Manager for HIL Scheduler.

This module provides simple utility functions for managing manual schedules:
- Random schedule generation
- CSV file loading
- Schedule preview generation

This is a stateless utility module - all state is maintained in the dashboard.
"""

import logging
import csv
import io
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pandas as pd
from scheduling.runtime import merge_schedule_frames, split_manual_override_series
from time_utils import (
    DEFAULT_TIMEZONE_NAME,
    get_timezone,
    normalize_datetime_series,
    normalize_schedule_index,
    normalize_timestamp_value,
    serialize_iso_with_tz,
)


MANUAL_SERIES_META = {
    "lib_p": {"plant_id": "lib", "signal": "p", "column": "power_setpoint_kw", "unit": "kW", "label": "LIB Active Power"},
    "lib_q": {
        "plant_id": "lib",
        "signal": "q",
        "column": "reactive_power_setpoint_kvar",
        "unit": "kvar",
        "label": "LIB Reactive Power",
    },
    "vrfb_p": {"plant_id": "vrfb", "signal": "p", "column": "power_setpoint_kw", "unit": "kW", "label": "VRFB Active Power"},
    "vrfb_q": {
        "plant_id": "vrfb",
        "signal": "q",
        "column": "reactive_power_setpoint_kvar",
        "unit": "kvar",
        "label": "VRFB Reactive Power",
    },
}
MANUAL_SERIES_KEYS = tuple(MANUAL_SERIES_META.keys())
MIN_MANUAL_ROW_GAP_S = 60


def _empty_manual_series_df():
    return pd.DataFrame(columns=["setpoint"])


def default_manual_end_time_map():
    return {key: None for key in MANUAL_SERIES_KEYS}


def manual_series_key(plant_id: str, signal: str) -> str:
    key = f"{str(plant_id).lower()}_{str(signal).lower()}"
    if key not in MANUAL_SERIES_META:
        raise KeyError(f"Unknown manual series key '{key}'")
    return key


def manual_series_keys_for_plant(plant_id: str):
    plant = str(plant_id).lower()
    return (manual_series_key(plant, "p"), manual_series_key(plant, "q"))


def default_manual_series_map():
    return {key: _empty_manual_series_df() for key in MANUAL_SERIES_KEYS}


def default_manual_merge_enabled_map(default_enabled: bool = False):
    return {key: bool(default_enabled) for key in MANUAL_SERIES_KEYS}


def normalize_manual_end_time(end_time, timezone_name: str = DEFAULT_TIMEZONE_NAME):
    if end_time is None:
        return None
    tz = get_timezone(timezone_name)
    ts = normalize_timestamp_value(end_time, tz)
    if pd.isna(ts):
        return None
    return ts


def _seconds_to_hms(total_s: int):
    total_s = max(0, int(total_s))
    return (total_s // 3600, (total_s % 3600) // 60, total_s % 60)


def _row_offset_seconds(row) -> int:
    return (
        int((row or {}).get("hours", 0) or 0) * 3600
        + int((row or {}).get("minutes", 0) or 0) * 60
        + int((row or {}).get("seconds", 0) or 0)
    )


def _is_end_editor_row(row) -> bool:
    return str((row or {}).get("kind", "value")) == "end"


def _force_editor_offsets_increasing(rows):
    if not rows:
        return rows
    previous_offset = None
    for idx, row in enumerate(rows):
        offset_s = 0 if idx == 0 else _row_offset_seconds(row)
        if previous_offset is not None:
            offset_s = max(offset_s, previous_offset + MIN_MANUAL_ROW_GAP_S)
        h, m, s = _seconds_to_hms(offset_s)
        row["hours"], row["minutes"], row["seconds"] = int(h), int(m), int(s)
        previous_offset = offset_s
    return rows


def ensure_manual_series_terminal_duplicate_row(series_df, timezone_name: str = DEFAULT_TIMEZONE_NAME):
    df = normalize_manual_series_df(series_df, timezone_name=timezone_name)
    if df.empty:
        return df

    tz = get_timezone(timezone_name)
    parts = split_manual_override_series(df, tz)
    norm = parts["series_df"]
    if norm.empty:
        return _empty_manual_series_df()
    if bool(parts["has_terminal_end"]):
        return norm

    last_ts = pd.Timestamp(norm.index[-1])
    last_value = float(norm.iloc[-1].get("setpoint", 0.0))
    tail = pd.DataFrame({"setpoint": [last_value]}, index=pd.DatetimeIndex([last_ts + pd.Timedelta(seconds=MIN_MANUAL_ROW_GAP_S)]))
    combined = pd.concat([norm, tail]).sort_index()
    return normalize_manual_series_df(combined, timezone_name=timezone_name)


def manual_series_end_timestamp(series_df, timezone_name: str = DEFAULT_TIMEZONE_NAME):
    tz = get_timezone(timezone_name)
    parts = split_manual_override_series(series_df, tz)
    return parts.get("end_ts")


def normalize_manual_series_df(series_df: pd.DataFrame, timezone_name: str = DEFAULT_TIMEZONE_NAME) -> pd.DataFrame:
    tz = get_timezone(timezone_name)
    if series_df is None or series_df.empty:
        return _empty_manual_series_df()

    df = series_df.copy()
    if "setpoint" not in df.columns:
        if len(df.columns) == 1:
            df = df.rename(columns={df.columns[0]: "setpoint"})
        else:
            raise ValueError("Manual series dataframe must contain a 'setpoint' column")

    if "datetime" in df.columns:
        df["datetime"] = normalize_datetime_series(df["datetime"], tz)
        df = df.dropna(subset=["datetime"]).set_index("datetime")

    df = normalize_schedule_index(df, tz)
    if df.empty:
        return _empty_manual_series_df()

    df = df[["setpoint"]].copy()
    df["setpoint"] = pd.to_numeric(df["setpoint"], errors="coerce")
    df = df.dropna(subset=["setpoint"])
    if df.empty:
        return _empty_manual_series_df()
    return df.sort_index()


def prune_manual_series_map_to_window(series_map, tz, window_start, window_end):
    pruned = {}
    for key in MANUAL_SERIES_KEYS:
        df = ensure_manual_series_terminal_duplicate_row(
            series_map.get(key),
            timezone_name=getattr(tz, "key", str(tz)),
        )
        if not df.empty:
            keep_mask = pd.Series(True, index=df.index)
            if window_start is not None:
                keep_mask = keep_mask & (df.index >= pd.Timestamp(window_start))
            if window_end is not None:
                keep_mask = keep_mask & (df.index < pd.Timestamp(window_end))
                end_ts = split_manual_override_series(df, tz).get("end_ts")
                if end_ts is not None and pd.Timestamp(end_ts) in df.index and pd.Timestamp(end_ts) >= pd.Timestamp(window_end):
                    keep_mask = keep_mask | (df.index == pd.Timestamp(end_ts))
            df = df.loc[keep_mask]
        pruned[key] = df if not df.empty else _empty_manual_series_df()
    return pruned


def prune_manual_end_time_map_to_window(end_time_map, tz, window_start, window_end):
    pruned = {}
    for key in MANUAL_SERIES_KEYS:
        ts = normalize_manual_end_time((end_time_map or {}).get(key), timezone_name=getattr(tz, "key", str(tz)))
        if ts is not None and window_start is not None and ts < pd.Timestamp(window_start):
            ts = None
        pruned[key] = ts
    return pruned


def rebuild_manual_schedule_df_by_plant(series_map, timezone_name: str = DEFAULT_TIMEZONE_NAME, end_time_by_key=None):
    tz = get_timezone(timezone_name)
    result = {"lib": pd.DataFrame(), "vrfb": pd.DataFrame()}
    for plant_id in result.keys():
        p_key, q_key = manual_series_keys_for_plant(plant_id)
        p_df = ensure_manual_series_terminal_duplicate_row(series_map.get(p_key), timezone_name=timezone_name)
        q_df = ensure_manual_series_terminal_duplicate_row(series_map.get(q_key), timezone_name=timezone_name)
        p_end_time = split_manual_override_series(p_df, tz).get("end_ts")
        q_end_time = split_manual_override_series(q_df, tz).get("end_ts")

        union_index = p_df.index.union(q_df.index).sort_values()
        if p_end_time is not None:
            union_index = union_index.union(pd.DatetimeIndex([pd.Timestamp(p_end_time)])).sort_values()
        if q_end_time is not None:
            union_index = union_index.union(pd.DatetimeIndex([pd.Timestamp(q_end_time)])).sort_values()
        if len(union_index) == 0:
            result[plant_id] = pd.DataFrame()
            continue

        combined = pd.DataFrame(index=union_index)
        if not p_df.empty:
            combined["power_setpoint_kw"] = p_df["setpoint"].reindex(union_index).ffill()
        else:
            combined["power_setpoint_kw"] = pd.Series(index=union_index, dtype=float)
        if p_end_time is not None:
            combined.loc[combined.index >= pd.Timestamp(p_end_time), "power_setpoint_kw"] = pd.NA
        if not q_df.empty:
            combined["reactive_power_setpoint_kvar"] = q_df["setpoint"].reindex(union_index).ffill()
        else:
            combined["reactive_power_setpoint_kvar"] = pd.Series(index=union_index, dtype=float)
        if q_end_time is not None:
            combined.loc[combined.index >= pd.Timestamp(q_end_time), "reactive_power_setpoint_kvar"] = pd.NA
        combined = normalize_schedule_index(combined, tz)
        result[plant_id] = combined
    return result


def manual_series_and_end_time_to_editor_rows_and_start(
    series_df: pd.DataFrame,
    end_time=None,
    timezone_name: str = DEFAULT_TIMEZONE_NAME,
):
    tz = get_timezone(timezone_name)
    df = ensure_manual_series_terminal_duplicate_row(series_df, timezone_name=timezone_name)
    if df.empty:
        return None, []
    parts = split_manual_override_series(df, tz)
    full_df = parts["series_df"]
    end_ts = parts.get("end_ts")
    if end_ts is None:
        full_df = ensure_manual_series_terminal_duplicate_row(full_df, timezone_name=timezone_name)
        parts = split_manual_override_series(full_df, tz)
        full_df = parts["series_df"]
        end_ts = parts.get("end_ts")

    start_ts = normalize_timestamp_value(full_df.index[0], tz)
    rows = []
    for idx, (ts, row) in enumerate(full_df.iterrows()):
        ts_norm = normalize_timestamp_value(ts, tz)
        total_s = int(round((ts_norm - start_ts).total_seconds()))
        hours = total_s // 3600
        minutes = (total_s % 3600) // 60
        seconds = total_s % 60
        is_terminal_end = end_ts is not None and idx == (len(full_df) - 1) and pd.Timestamp(ts) == pd.Timestamp(end_ts)
        rows.append(
            {
                "hours": int(hours),
                "minutes": int(minutes),
                "seconds": int(seconds),
                "setpoint": None if is_terminal_end else float(row.get("setpoint", 0.0)),
                "kind": "end" if is_terminal_end else "value",
            }
        )
    rows = _normalize_editor_rows(rows)
    return start_ts, rows


def manual_series_df_to_editor_rows_and_start(
    series_df: pd.DataFrame,
    timezone_name: str = DEFAULT_TIMEZONE_NAME,
):
    return manual_series_and_end_time_to_editor_rows_and_start(
        series_df,
        end_time=None,
        timezone_name=timezone_name,
    )


def _normalize_editor_rows(rows):
    normalized_rows = []
    if rows is None:
        return normalized_rows
    rows_list = list(rows)
    row_count = len(rows_list)
    if row_count == 0:
        return []

    explicit_end_indices = []
    for idx, row in enumerate(rows_list):
        if not isinstance(row, dict):
            raise ValueError(f"Row {idx + 1}: invalid row format")
        try:
            hours = int(row.get("hours", 0))
            minutes = int(row.get("minutes", 0))
            seconds = int(row.get("seconds", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Row {idx + 1}: time values must be integers") from exc
        if hours < 0:
            raise ValueError(f"Row {idx + 1}: hours must be >= 0")
        if minutes < 0 or minutes > 59:
            raise ValueError(f"Row {idx + 1}: minutes must be between 0 and 59")
        if seconds < 0 or seconds > 59:
            raise ValueError(f"Row {idx + 1}: seconds must be between 0 and 59")
        kind_raw = str(row.get("kind", "value") or "value").strip().lower()
        kind = "end" if kind_raw == "end" else "value"
        raw_setpoint = row.get("setpoint")
        if kind == "end":
            explicit_end_indices.append(idx)
            setpoint = None
        else:
            if isinstance(raw_setpoint, str) and str(raw_setpoint).strip().lower() == "end":
                kind = "end"
                explicit_end_indices.append(idx)
                setpoint = None
            else:
                try:
                    setpoint = float(raw_setpoint)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"Row {idx + 1}: setpoint must be numeric") from exc
        normalized_rows.append(
            {
                "hours": hours,
                "minutes": minutes,
                "seconds": seconds,
                "setpoint": setpoint,
                "kind": kind,
            }
        )

    if explicit_end_indices:
        if len(explicit_end_indices) > 1:
            raise ValueError("Only one end row is allowed")
        if explicit_end_indices[0] != (len(normalized_rows) - 1):
            raise ValueError(f"Row {explicit_end_indices[0] + 1}: end row must be the last row")

    if normalized_rows:
        first = normalized_rows[0]
        if str(first.get("kind", "value")) == "end":
            raise ValueError("First row cannot be an end row")
        if (first["hours"], first["minutes"], first["seconds"]) != (0, 0, 0):
            raise ValueError("First row must start at 00:00:00")
        normalized_rows[0]["hours"] = 0
        normalized_rows[0]["minutes"] = 0
        normalized_rows[0]["seconds"] = 0

    if not _is_end_editor_row(normalized_rows[-1]):
        if len(normalized_rows) >= 2:
            prev = normalized_rows[-2]
            last = normalized_rows[-1]
            if (not _is_end_editor_row(prev)) and (not _is_end_editor_row(last)) and float(last["setpoint"]) == float(prev["setpoint"]):
                normalized_rows[-1]["kind"] = "end"
                normalized_rows[-1]["setpoint"] = None
            else:
                last_offset = _row_offset_seconds(normalized_rows[-1])
                h, m, s = _seconds_to_hms(last_offset + MIN_MANUAL_ROW_GAP_S)
                normalized_rows.append({"hours": h, "minutes": m, "seconds": s, "setpoint": None, "kind": "end"})
        else:
            last_offset = _row_offset_seconds(normalized_rows[-1])
            h, m, s = _seconds_to_hms(last_offset + MIN_MANUAL_ROW_GAP_S)
            normalized_rows.append({"hours": h, "minutes": m, "seconds": s, "setpoint": None, "kind": "end"})

    normalized_rows = _force_editor_offsets_increasing(normalized_rows)
    return normalized_rows


def manual_editor_rows_to_series_df(rows, start_time, timezone_name: str = DEFAULT_TIMEZONE_NAME) -> pd.DataFrame:
    series_df, _ = manual_editor_rows_to_series_and_end_time(rows, start_time, timezone_name=timezone_name)
    return series_df


def manual_editor_rows_to_series_and_end_time(rows, start_time, timezone_name: str = DEFAULT_TIMEZONE_NAME):
    tz = get_timezone(timezone_name)
    normalized_rows = _normalize_editor_rows(rows)
    if not normalized_rows:
        return _empty_manual_series_df(), None

    if start_time is None:
        raise ValueError("Start datetime is required when rows are not empty")
    start_ts = normalize_timestamp_value(start_time, tz)
    if pd.isna(start_ts):
        raise ValueError("Invalid start datetime")

    data = []
    end_time = None
    previous_value = None
    for row in normalized_rows:
        offset_s = (row["hours"] * 3600) + (row["minutes"] * 60) + row["seconds"]
        row_ts = start_ts + pd.Timedelta(seconds=offset_s)
        if str(row.get("kind", "value")) == "end":
            end_time = row_ts
            if previous_value is None:
                raise ValueError("End row requires at least one value breakpoint")
            data.append({"datetime": row_ts, "setpoint": float(previous_value)})
            continue
        previous_value = float(row["setpoint"])
        data.append({"datetime": row_ts, "setpoint": previous_value})
    if not data:
        return _empty_manual_series_df(), None
    df = pd.DataFrame(data).set_index("datetime")
    norm_df = ensure_manual_series_terminal_duplicate_row(df, timezone_name=timezone_name)
    return norm_df, None


def manual_editor_rows_to_relative_csv_text(rows) -> str:
    normalized_rows = _normalize_editor_rows(rows)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["hours", "minutes", "seconds", "setpoint"])
    for row in normalized_rows:
        value = "end" if str(row.get("kind", "value")) == "end" else row["setpoint"]
        writer.writerow([row["hours"], row["minutes"], row["seconds"], value])
    return buffer.getvalue()


def load_manual_editor_rows_from_relative_csv_text(csv_text: str):
    try:
        df = pd.read_csv(io.StringIO(csv_text))
    except Exception as exc:
        raise ValueError(f"Could not read CSV: {exc}") from exc

    # Accept harmless header variations (case/whitespace/BOM), validate by structure.
    normalized_name_map = {}
    for column in df.columns:
        normalized = str(column).replace("\ufeff", "").strip().lower()
        normalized_name_map[normalized] = column

    required = ["hours", "minutes", "seconds", "setpoint"]
    missing = [col for col in required if col not in normalized_name_map]
    if missing:
        raise ValueError(f"CSV missing required columns: {', '.join(missing)}")
    rows = df[[normalized_name_map[col] for col in required]].copy()
    rows.columns = required
    rows = rows.to_dict("records")
    return _normalize_editor_rows(rows)


def generate_random_schedule(
    start_time: datetime,
    end_time: datetime,
    step_minutes: int = 5,
    min_power_kw: float = -1000.0,
    max_power_kw: float = 1000.0,
    reactive_power_kvar: float = 0.0,
    timezone_name: str = DEFAULT_TIMEZONE_NAME,
) -> pd.DataFrame:
    """
    Generate a random schedule DataFrame.
    
    Args:
        start_time: Schedule start time
        end_time: Schedule end time
        step_minutes: Time resolution in minutes
        min_power_kw: Minimum power (kW)
        max_power_kw: Maximum power (kW)
        reactive_power_kvar: Reactive power setpoint (kvar)
    
    Returns:
        DataFrame with datetime index and power_setpoint_kw, reactive_power_setpoint_kvar columns
    """
    tz = get_timezone(timezone_name)
    start_time = normalize_timestamp_value(start_time, tz)
    end_time = normalize_timestamp_value(end_time, tz)

    duration_hours = (end_time - start_time).total_seconds() / 3600
    num_points = int(duration_hours * 60 / step_minutes) + 1
    
    timestamps = pd.date_range(start=start_time, periods=num_points, freq=f'{step_minutes}min')
    
    # Ensure we don't exceed end_time
    timestamps = timestamps[timestamps <= end_time]
    
    # Generate random power setpoints
    power_values = np.random.uniform(min_power_kw, max_power_kw, size=len(timestamps))
    
    # Ensure last setpoint is zero for predictable end state
    if len(power_values) > 0:
        power_values[-1] = 0
    
    df = pd.DataFrame({
        'power_setpoint_kw': power_values,
        'reactive_power_setpoint_kvar': reactive_power_kvar
    }, index=timestamps)
    
    df.index.name = 'datetime'
    
    logging.info(f"Generated random schedule: {len(df)} points from {start_time} to {end_time}")
    return df


def load_csv_schedule(
    csv_path: str,
    start_time: Optional[datetime] = None,
    reactive_power_kvar: float = 0.0,
    timezone_name: str = DEFAULT_TIMEZONE_NAME,
) -> pd.DataFrame:
    """
    Load a schedule from a CSV file.
    
    Args:
        csv_path: Path to the CSV file
        start_time: Start time to use (if None, uses the first timestamp in CSV)
        reactive_power_kvar: Default reactive power if not in CSV
    
    Returns:
        DataFrame with datetime index and power_setpoint_kw, reactive_power_setpoint_kvar columns
    
    Raises:
        FileNotFoundError: If the CSV file doesn't exist
        ValueError: If the CSV is missing required columns
    """
    csv_file = Path(csv_path)
    if not csv_file.exists():
        raise FileNotFoundError(f"Schedule file not found: {csv_path}")
    
    # Read CSV
    tz = get_timezone(timezone_name)
    df = pd.read_csv(csv_path, parse_dates=['datetime'])
    df['datetime'] = normalize_datetime_series(df['datetime'], tz)
    df = df.dropna(subset=['datetime'])
    
    # Ensure required columns exist
    if 'power_setpoint_kw' not in df.columns:
        raise ValueError("CSV must contain 'power_setpoint_kw' column")
    
    if 'reactive_power_setpoint_kvar' not in df.columns:
        df['reactive_power_setpoint_kvar'] = reactive_power_kvar
    
    # Handle start_time offset
    if start_time is not None:
        start_time = normalize_timestamp_value(start_time, tz)
        # Calculate the offset from the first timestamp in the file
        first_ts = df['datetime'].iloc[0]
        offset = start_time - first_ts
        
        # Add offset to all timestamps
        df['datetime'] = df['datetime'] + offset
    
    # Set datetime as index
    df = df.set_index('datetime')
    df = normalize_schedule_index(df, tz)
    
    logging.info(f"Loaded CSV schedule: {len(df)} points from {csv_path}")
    return df


def append_schedules(
    existing_df: pd.DataFrame,
    new_df: pd.DataFrame,
    replace_overlapping: bool = True,
    timezone_name: str = DEFAULT_TIMEZONE_NAME,
) -> pd.DataFrame:
    """
    Append new schedule data to existing schedule, replacing overlapping periods.
    
    Args:
        existing_df: Existing schedule DataFrame
        new_df: New schedule DataFrame to append
        replace_overlapping: If True, replace existing data for overlapping periods
    
    Returns:
        Combined DataFrame
    """
    tz = get_timezone(timezone_name)

    if existing_df.empty:
        return normalize_schedule_index(new_df, tz)
    
    if new_df.empty:
        return normalize_schedule_index(existing_df, tz)

    existing_df = normalize_schedule_index(existing_df, tz)
    new_df = normalize_schedule_index(new_df, tz)

    if replace_overlapping:
        combined = merge_schedule_frames(existing_df, new_df)
    else:
        combined = pd.concat([existing_df, new_df]).sort_index()
    
    logging.info(f"Appended schedules: existing={len(existing_df)}, new={len(new_df)}, combined={len(combined)}")
    return combined


def get_current_setpoint(
    schedule_df: pd.DataFrame,
    current_time: Optional[datetime] = None,
    timezone_name: str = DEFAULT_TIMEZONE_NAME,
) -> Tuple[float, float]:
    """
    Get the current setpoint for the given time using asof lookup.
    
    Args:
        schedule_df: Schedule DataFrame
        current_time: The current time (default: now)
    
    Returns:
        Tuple of (power_kw, reactive_power_kvar) or (0.0, 0.0) if no data
    """
    tz = get_timezone(timezone_name)

    if current_time is None:
        current_time = datetime.now(tz)
    else:
        current_time = normalize_timestamp_value(current_time, tz)
    
    if schedule_df.empty:
        return 0.0, 0.0

    schedule_df = normalize_schedule_index(schedule_df, tz)
    
    # Use asof to find the value just before current time
    row = schedule_df.asof(current_time)
    
    if pd.isna(row).all():
        return 0.0, 0.0
    
    power = row.get('power_setpoint_kw', 0.0)
    q_power = row.get('reactive_power_setpoint_kvar', 0.0)
    
    return power, q_power


def schedule_to_dict(schedule_df: pd.DataFrame, timezone_name: str = DEFAULT_TIMEZONE_NAME) -> dict:
    """
    Convert a schedule DataFrame to a dictionary format.
    
    Args:
        schedule_df: Schedule DataFrame
    
    Returns:
        Dictionary with ISO datetime keys and power values
    """
    if schedule_df.empty:
        return {}
    
    tz = get_timezone(timezone_name)

    result = {}
    for timestamp, row in schedule_df.iterrows():
        dt_str = serialize_iso_with_tz(timestamp, tz=tz)
        result[dt_str] = row['power_setpoint_kw']
    
    return result


def create_schedule_dataframe(
    schedule_dict: dict,
    default_q_kvar: float = 0.0,
    timezone_name: str = DEFAULT_TIMEZONE_NAME,
) -> pd.DataFrame:
    """
    Create a schedule DataFrame from a dictionary.
    
    Args:
        schedule_dict: Dictionary with ISO datetime keys and power values
        default_q_kvar: Default reactive power value
    
    Returns:
        DataFrame with datetime index
    """
    if not schedule_dict:
        return pd.DataFrame(columns=['power_setpoint_kw', 'reactive_power_setpoint_kvar'])
    
    tz = get_timezone(timezone_name)

    data = []
    for dt_str, power_kw in schedule_dict.items():
        # Parse ISO format datetime
        if '+' in dt_str or 'Z' in dt_str:
            dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
        else:
            dt = datetime.fromisoformat(dt_str)
        
        data.append({
            'datetime': normalize_timestamp_value(dt, tz),
            'power_setpoint_kw': power_kw,
            'reactive_power_setpoint_kvar': default_q_kvar
        })
    
    df = pd.DataFrame(data).set_index('datetime')
    df = df.sort_index()
    df = normalize_schedule_index(df, tz)
    
    return df


if __name__ == "__main__":
    # Test the module
    logging.basicConfig(level=logging.INFO)
    
    print("=== Testing Random Schedule Generation ===")
    start = datetime.now().replace(minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=2)
    
    df = generate_random_schedule(start, end, step_minutes=15, min_power_kw=-500, max_power_kw=500)
    print(f"Generated {len(df)} points")
    print(df.head())
    
    print("\n=== Testing Current Setpoint Lookup ===")
    power, q_power = get_current_setpoint(df)
    print(f"Current setpoint: P={power:.2f} kW, Q={q_power:.2f} kvar")
    
    print("\n=== Testing Schedule to Dict ===")
    schedule_dict = schedule_to_dict(df)
    print(f"Dictionary has {len(schedule_dict)} entries")
    print(f"First entry: {list(schedule_dict.items())[0]}")
    
    print("\nManual Schedule Manager test complete.")
