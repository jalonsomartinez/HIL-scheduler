"""Helpers for historical measurement browsing in the dashboard."""

import os
import re
from typing import Any

import pandas as pd

from measurement.storage import MEASUREMENT_COLUMNS, load_file_for_cache
from time_utils import normalize_timestamp_value, serialize_iso_with_tz


_HISTORY_FILE_RE = re.compile(r"^\d{8}_(?P<suffix>[a-z0-9_-]+)\.csv$", re.IGNORECASE)


def _ts_to_epoch_ms(value: Any, tz) -> int | None:
    ts = normalize_timestamp_value(value, tz)
    if pd.isna(ts):
        return None
    return int(ts.value // 1_000_000)


def _epoch_ms_to_ts(value: Any, tz) -> pd.Timestamp:
    return normalize_timestamp_value(pd.to_datetime(int(value), unit="ms", utc=True), tz)


def scan_measurement_history_index(data_dir, plant_suffix_by_id, tz):
    """Scan measurement CSV files and return JSON-safe index metadata."""
    files_by_plant = {plant_id: [] for plant_id in plant_suffix_by_id}
    suffix_to_plant = {str(suffix).lower(): plant_id for plant_id, suffix in plant_suffix_by_id.items()}

    try:
        filenames = sorted(os.listdir(data_dir))
    except FileNotFoundError:
        filenames = []
    except Exception:
        filenames = []

    global_start_ms = None
    global_end_ms = None

    for filename in filenames:
        match = _HISTORY_FILE_RE.match(filename)
        if not match:
            continue

        plant_id = suffix_to_plant.get(match.group("suffix").lower())
        if plant_id is None:
            continue

        file_path = os.path.join(data_dir, filename)
        if not os.path.isfile(file_path):
            continue

        df = load_file_for_cache(file_path, tz)
        if df.empty or "timestamp" not in df.columns:
            continue

        start_ts = df["timestamp"].min()
        end_ts = df["timestamp"].max()
        start_ms = _ts_to_epoch_ms(start_ts, tz)
        end_ms = _ts_to_epoch_ms(end_ts, tz)
        if start_ms is None or end_ms is None:
            continue

        rows = int(len(df))
        item = {
            "path": file_path,
            "start_ms": int(start_ms),
            "end_ms": int(end_ms),
            "rows": rows,
        }
        files_by_plant[plant_id].append(item)

        global_start_ms = start_ms if global_start_ms is None else min(global_start_ms, start_ms)
        global_end_ms = end_ms if global_end_ms is None else max(global_end_ms, end_ms)

    for plant_id in files_by_plant:
        files_by_plant[plant_id].sort(key=lambda item: (item["start_ms"], item["path"]))

    has_data = global_start_ms is not None and global_end_ms is not None
    return {
        "has_data": bool(has_data),
        "tz_name": getattr(tz, "key", str(tz)),
        "global_start_ms": int(global_start_ms) if has_data else None,
        "global_end_ms": int(global_end_ms) if has_data else None,
        "files_by_plant": files_by_plant,
    }


def clamp_epoch_range(selected, domain_min, domain_max):
    """Clamp or default a selected range to the slider domain."""
    if domain_min is None or domain_max is None:
        return None

    lo = int(min(domain_min, domain_max))
    hi = int(max(domain_min, domain_max))
    if selected is None or not isinstance(selected, (list, tuple)) or len(selected) != 2:
        return [lo, hi]

    try:
        a = int(selected[0])
        b = int(selected[1])
    except (TypeError, ValueError):
        return [lo, hi]

    # Treat fully out-of-domain selections as stale placeholders (for example
    # the layout's initial [0, 1] before real history bounds are known).
    if (a < lo and b < lo) or (a > hi and b > hi):
        return [lo, hi]

    start = max(lo, min(a, hi))
    end = max(lo, min(b, hi))
    if start > end:
        start, end = end, start
    return [start, end]


def build_slider_marks(start_ms, end_ms, tz, max_marks=8):
    """Build sparse slider marks for a time range."""
    if start_ms is None or end_ms is None:
        return {}

    start_ms = int(start_ms)
    end_ms = int(end_ms)
    if end_ms < start_ms:
        start_ms, end_ms = end_ms, start_ms

    if start_ms == end_ms:
        label = _epoch_ms_to_ts(start_ms, tz).strftime("%m-%d %H:%M")
        return {start_ms: label}

    count = max(2, int(max_marks or 8))
    span = end_ms - start_ms
    if span <= count - 1:
        values = list(range(start_ms, end_ms + 1))
    else:
        values = []
        for idx in range(count):
            value = start_ms + round(span * idx / (count - 1))
            values.append(int(value))
        values = sorted(set(values))

    marks = {}
    for value in values:
        ts = _epoch_ms_to_ts(value, tz)
        marks[int(value)] = ts.strftime("%m-%d %H:%M")
    return marks


def load_cropped_measurements_for_range(file_meta_list, start_ms, end_ms, tz):
    """Load overlapping files and crop to the inclusive selected range."""
    if not file_meta_list:
        return pd.DataFrame(columns=MEASUREMENT_COLUMNS)

    orig_start_ms = int(start_ms)
    orig_end_ms = int(end_ms)
    start_ms = int(min(orig_start_ms, orig_end_ms))
    end_ms = int(max(orig_start_ms, orig_end_ms))
    start_ts = _epoch_ms_to_ts(start_ms, tz)
    end_ts = _epoch_ms_to_ts(end_ms, tz)

    frames = []
    for item in file_meta_list:
        item_start = int(item.get("start_ms", 0))
        item_end = int(item.get("end_ms", 0))
        if item_end < start_ms or item_start > end_ms:
            continue
        path = item.get("path")
        if not path:
            continue
        df = load_file_for_cache(path, tz)
        if df.empty:
            continue
        if "timestamp" not in df.columns:
            continue
        mask = (df["timestamp"] >= start_ts) & (df["timestamp"] <= end_ts)
        cropped = df.loc[mask, [c for c in MEASUREMENT_COLUMNS if c in df.columns]].copy()
        if not cropped.empty:
            for col in MEASUREMENT_COLUMNS:
                if col not in cropped.columns:
                    cropped[col] = pd.NA
            frames.append(cropped[MEASUREMENT_COLUMNS])

    if not frames:
        return pd.DataFrame(columns=MEASUREMENT_COLUMNS)

    result = pd.concat(frames, ignore_index=True)
    result = result.sort_values("timestamp").reset_index(drop=True)
    return result[MEASUREMENT_COLUMNS]


def serialize_measurements_for_download(df, tz):
    """Return a dataframe ready for CSV download with ISO timestamps."""
    if df is None or df.empty:
        return pd.DataFrame(columns=MEASUREMENT_COLUMNS)

    result = df.copy()
    for col in MEASUREMENT_COLUMNS:
        if col not in result.columns:
            result[col] = pd.NA
    result = result[MEASUREMENT_COLUMNS].copy()
    result["timestamp"] = result["timestamp"].apply(lambda value: serialize_iso_with_tz(value, tz=tz))
    return result
