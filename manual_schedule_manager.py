"""
Manual Schedule Manager for HIL Scheduler.

This module provides simple utility functions for managing manual schedules:
- Random schedule generation
- CSV file loading
- Schedule preview generation

This is a stateless utility module - all state is maintained in the dashboard.
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pandas as pd
from time_utils import (
    DEFAULT_TIMEZONE_NAME,
    get_timezone,
    normalize_datetime_series,
    normalize_schedule_index,
    normalize_timestamp_value,
    serialize_iso_with_tz,
)


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
        # Remove overlapping rows from existing schedule
        non_overlapping = existing_df.index.difference(new_df.index)
        existing_df = existing_df.loc[non_overlapping]
    
    # Concatenate and sort
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
