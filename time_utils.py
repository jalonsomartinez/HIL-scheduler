"""Timezone helpers for consistent timestamp handling across agents."""

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd


DEFAULT_TIMEZONE_NAME = "Europe/Madrid"


def get_timezone(timezone_name: str) -> ZoneInfo:
    """Return a valid ZoneInfo object, falling back to default timezone."""
    try:
        return ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        return ZoneInfo(DEFAULT_TIMEZONE_NAME)


def get_config_tz(config: dict) -> ZoneInfo:
    """Return timezone configured in config, defaulting safely."""
    timezone_name = config.get("TIMEZONE_NAME", DEFAULT_TIMEZONE_NAME)
    return get_timezone(timezone_name)


def now_tz(config: dict) -> datetime:
    """Return timezone-aware current datetime in configured timezone."""
    return datetime.now(get_config_tz(config))


def normalize_timestamp_value(value: Any, tz: ZoneInfo, naive_policy: str = "config_tz") -> pd.Timestamp:
    """
    Normalize a single timestamp-like value to the configured timezone.

    Policy for naive timestamps:
    - "config_tz" (default): interpret naive values as configured timezone.
    - "utc": interpret naive values as UTC then convert to configured timezone.
    """
    if value is None:
        return pd.NaT

    ts = pd.Timestamp(value)
    if pd.isna(ts):
        return pd.NaT

    if ts.tzinfo is None:
        if naive_policy == "utc":
            ts = ts.tz_localize(timezone.utc)
        else:
            ts = ts.tz_localize(tz)
    return ts.tz_convert(tz)


def normalize_datetime_series(series: pd.Series, tz: ZoneInfo, naive_policy: str = "config_tz") -> pd.Series:
    """Normalize a series of mixed datetime values to configured timezone."""
    normalized = [normalize_timestamp_value(value, tz, naive_policy=naive_policy) for value in series]
    return pd.Series(normalized, index=series.index)


def normalize_schedule_index(df: pd.DataFrame, tz: ZoneInfo, naive_policy: str = "config_tz") -> pd.DataFrame:
    """Return copy of dataframe with timezone-normalized datetime index."""
    if df is None:
        return pd.DataFrame()
    if df.empty:
        return df.copy()

    result = df.copy()
    normalized_index = [
        normalize_timestamp_value(value, tz, naive_policy=naive_policy) for value in result.index
    ]
    dt_index = pd.DatetimeIndex(normalized_index)
    valid_mask = ~dt_index.isna()
    if not valid_mask.any():
        return result.iloc[0:0].copy()

    result = result.loc[valid_mask].copy()
    result.index = dt_index[valid_mask]
    result = result.sort_index()
    return result


def serialize_iso_with_tz(value: Any, tz: ZoneInfo = None, naive_policy: str = "config_tz") -> str:
    """Serialize timestamp-like value as ISO 8601 string with timezone offset."""
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        return ""

    if tz is not None:
        ts = normalize_timestamp_value(ts, tz, naive_policy=naive_policy)
    elif ts.tzinfo is None:
        ts = ts.tz_localize(timezone.utc)

    return ts.isoformat()
