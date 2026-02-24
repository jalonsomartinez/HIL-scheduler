"""Measurement recording and cache primitives."""

import logging
import os
import re

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
_DAILY_MEASUREMENT_FILE_RE = re.compile(r"^(?P<date>\d{8})_(?P<suffix>[a-z0-9_-]+)\.csv$", re.IGNORECASE)


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


def find_latest_persisted_soc_for_plant(data_dir, plant_name, plant_id, tz):
    """Return latest persisted non-null SoC row metadata for one plant, or None."""
    safe_name = sanitize_plant_name(plant_name, plant_id)

    try:
        filenames = sorted(os.listdir(data_dir), reverse=True)
    except FileNotFoundError:
        return None
    except Exception as exc:
        logging.error("Measurement: error listing %s: %s", data_dir, exc)
        return None

    latest = None
    for filename in filenames:
        match = _DAILY_MEASUREMENT_FILE_RE.match(filename)
        if not match:
            continue
        if str(match.group("suffix")).lower() != str(safe_name).lower():
            continue

        file_path = os.path.join(data_dir, filename)
        if not os.path.isfile(file_path):
            continue

        df = load_file_for_cache(file_path, tz)
        if df.empty or "timestamp" not in df.columns or "soc_pu" not in df.columns:
            continue

        real_soc = df.dropna(subset=["timestamp", "soc_pu"])
        if real_soc.empty:
            continue

        row = real_soc.iloc[-1]
        try:
            soc_pu = float(row["soc_pu"])
        except (TypeError, ValueError):
            continue
        if pd.isna(soc_pu):
            continue

        soc_pu = min(1.0, max(0.0, soc_pu))
        timestamp = normalize_timestamp_value(row.get("timestamp"), tz)
        if pd.isna(timestamp):
            continue

        candidate = {
            "soc_pu": soc_pu,
            "timestamp": timestamp,
            "file_path": file_path,
        }
        if latest is None or pd.Timestamp(candidate["timestamp"]) > pd.Timestamp(latest["timestamp"]):
            latest = candidate

    return latest


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
