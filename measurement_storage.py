"""Measurement recording and cache primitives."""

import logging
import os

import numpy as np
import pandas as pd

from runtime_contracts import sanitize_plant_name
from time_utils import normalize_datetime_series, normalize_timestamp_value, serialize_iso_with_tz

MEASUREMENT_VALUE_COLUMNS = [
    "p_setpoint_kw",
    "battery_active_power_kw",
    "q_setpoint_kvar",
    "battery_reactive_power_kvar",
    "soc_pu",
    "p_poi_kw",
    "q_poi_kvar",
    "v_poi_kV",
]
MEASUREMENT_COLUMNS = ["timestamp"] + MEASUREMENT_VALUE_COLUMNS


def normalize_measurements_df(df, tz):
    if df is None or df.empty:
        return pd.DataFrame(columns=MEASUREMENT_COLUMNS)

    result = df.copy()
    for column in MEASUREMENT_COLUMNS:
        if column not in result.columns:
            result[column] = np.nan
    result["timestamp"] = normalize_datetime_series(result["timestamp"], tz)
    result = result.dropna(subset=["timestamp"])
    return result[MEASUREMENT_COLUMNS].sort_values("timestamp").reset_index(drop=True)


def build_daily_file_path(plant_name, fallback, timestamp, tz, now_ts):
    safe_name = sanitize_plant_name(plant_name, fallback)
    ts = normalize_timestamp_value(timestamp, tz)
    if pd.isna(ts):
        ts = pd.Timestamp(now_ts)
    return os.path.join("data", f"{ts.strftime('%Y%m%d')}_{safe_name}.csv")


def append_rows_to_csv(file_path, rows, tz):
    if not rows:
        return

    os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
    df = normalize_measurements_df(pd.DataFrame(rows), tz)
    if df.empty:
        return

    write_header = (not os.path.exists(file_path)) or os.path.getsize(file_path) == 0
    df["timestamp"] = df["timestamp"].apply(lambda value: serialize_iso_with_tz(value, tz=tz))
    df.to_csv(file_path, mode="a", header=write_header, index=False)


def load_file_for_cache(file_path, tz):
    if not os.path.exists(file_path):
        return pd.DataFrame(columns=MEASUREMENT_COLUMNS)
    try:
        return normalize_measurements_df(pd.read_csv(file_path), tz)
    except Exception as exc:
        logging.error("Measurement: error reading %s: %s", file_path, exc)
        return pd.DataFrame(columns=MEASUREMENT_COLUMNS)


def is_null_row(row):
    return all(pd.isna(row.get(column)) for column in MEASUREMENT_VALUE_COLUMNS)


def is_real_row(row):
    return not is_null_row(row)


def rows_are_similar(prev_row, new_row, tolerances):
    for column in MEASUREMENT_VALUE_COLUMNS:
        prev_value = prev_row.get(column)
        new_value = new_row.get(column)
        if pd.isna(prev_value) or pd.isna(new_value):
            return False
        try:
            tolerance = float(tolerances.get(column, 0.0))
            if tolerance < 0:
                tolerance = 0.0
            if abs(float(new_value) - float(prev_value)) > tolerance:
                return False
        except (TypeError, ValueError):
            return False
    return True


def build_null_row(timestamp, tz):
    row = {"timestamp": normalize_timestamp_value(timestamp, tz)}
    for column in MEASUREMENT_VALUE_COLUMNS:
        row[column] = np.nan
    return row
