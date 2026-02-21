"""Measurement posting queue utility helpers."""

import math

import pandas as pd

from time_utils import normalize_timestamp_value


def finite_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def to_utc_iso(value, tz):
    ts = normalize_timestamp_value(value, tz)
    if pd.isna(ts):
        return None
    return ts.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%S+00:00")


def build_post_items(row, model, series, tz):
    """Build normalized post payload entries for soc/p/q/v metrics."""
    timestamp_iso = to_utc_iso(row.get("timestamp"), tz)
    if timestamp_iso is None:
        return []

    capacity_kwh = finite_float(model.get("capacity_kwh"))
    poi_voltage_v = finite_float(model.get("poi_voltage_v"))

    soc_pu = finite_float(row.get("soc_pu"))
    p_poi_kw = finite_float(row.get("p_poi_kw"))
    q_poi_kvar = finite_float(row.get("q_poi_kvar"))
    v_poi_pu = finite_float(row.get("v_poi_pu"))

    soc_value = soc_pu * capacity_kwh if soc_pu is not None and capacity_kwh is not None else None
    p_value = p_poi_kw * 1000.0 if p_poi_kw is not None else None
    q_value = q_poi_kvar * 1000.0 if q_poi_kvar is not None else None
    v_value = v_poi_pu * poi_voltage_v if v_poi_pu is not None and poi_voltage_v is not None else None

    return [
        ("soc", series.get("soc"), soc_value, timestamp_iso),
        ("p", series.get("p"), p_value, timestamp_iso),
        ("q", series.get("q"), q_value, timestamp_iso),
        ("v", series.get("v"), v_value, timestamp_iso),
    ]
