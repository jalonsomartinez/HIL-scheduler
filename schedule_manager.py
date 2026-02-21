"""
Schedule Manager for HIL Scheduler.

This module provides a unified interface for managing schedules across all three modes:
1. Random schedule generation
2. CSV file upload
3. Istentore API fetch

The schedule manager handles:
- Mode switching
- Schedule generation/loading
- Data replacement (only overlapping periods)
- API polling for next-day schedules
"""

import logging
import threading
import time
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any, Callable

import numpy as np
import pandas as pd

from istentore_api import IstentoreAPI
from schedule_runtime import merge_schedule_frames


class ScheduleMode(Enum):
    """Enumeration of schedule source modes."""
    MANUAL = "manual"
    RANDOM = "random"
    CSV = "csv"
    API = "api"


class ScheduleManager:
    """
    Central manager for handling schedules across all modes.
    
    This class provides a unified interface for:
    - Generating random schedules (Mode 1)
    - Loading schedules from CSV files (Mode 2)
    - Fetching schedules from the Istentore API (Mode 3)
    
    The schedule DataFrame uses flexible time resolution (matches source data)
    and supports appending new data that replaces only overlapping periods.
    """
    
    def __init__(self, config: dict):
        """
        Initialize the schedule manager.
        
        Args:
            config: Configuration dictionary with schedule settings
        """
        self.config = config
        self._schedule_df = pd.DataFrame()
        self._mode: Optional[ScheduleMode] = None
        self._api: Optional[IstentoreAPI] = None
        self._api_polling_thread: Optional[threading.Thread] = None
        self._shutdown_event = threading.Event()
        self._lock = threading.Lock()
        
        # Store callbacks for dashboard integration
        self._on_schedule_update: Optional[Callable] = None
        
        # API polling settings from config
        self._poll_interval_min = config.get('ISTENTORE_POLL_INTERVAL_MIN', 10)
        self._poll_start_time = config.get('ISTENTORE_POLL_START_TIME', "17:30")
    
    def set_on_schedule_update_callback(self, callback: Callable):
        """
        Set a callback to be called when the schedule is updated.
        
        Args:
            callback: Function to call on schedule updates
        """
        self._on_schedule_update = callback
    
    @property
    def schedule_df(self) -> pd.DataFrame:
        """Get the current schedule DataFrame."""
        with self._lock:
            return self._schedule_df.copy()
    
    @property
    def mode(self) -> Optional[ScheduleMode]:
        """Get the current schedule mode."""
        return self._mode
    
    @property
    def is_empty(self) -> bool:
        """Check if the schedule is empty."""
        with self._lock:
            return self._schedule_df.empty
    
    @property
    def start_time(self) -> Optional[datetime]:
        """Get the start time of the schedule."""
        with self._lock:
            if self._schedule_df.empty:
                return None
            return self._schedule_df.index.min()
    
    @property
    def end_time(self) -> Optional[datetime]:
        """Get the end time of the schedule."""
        with self._lock:
            if self._schedule_df.empty:
                return None
            return self._schedule_df.index.max()
    
    def clear_schedule(self):
        """Clear all schedule data and stop any running processes."""
        logging.info("ScheduleManager: Clearing schedule")
        
        # Stop API polling
        self._shutdown_event.set()
        if self._api_polling_thread and self._api_polling_thread.is_alive():
            self._api_polling_thread.join(timeout=5)
        
        self._shutdown_event.clear()
        self._api_polling_thread = None
        
        with self._lock:
            self._schedule_df = pd.DataFrame()
        
        self._mode = None
        self._api = None
        
        # Notify callback
        if self._on_schedule_update:
            self._on_schedule_update()
    
    def set_mode(self, mode: ScheduleMode, **kwargs):
        """
        Switch to a new schedule mode.
        
        Args:
            mode: The new schedule mode
            **kwargs: Mode-specific parameters
        """
        logging.info(f"ScheduleManager: Switching to mode {mode.value}")
        
        # Clear existing schedule when switching modes
        self.clear_schedule()
        
        self._mode = mode
        
        if mode == ScheduleMode.RANDOM:
            self._generate_random_schedule(**kwargs)
        elif mode == ScheduleMode.CSV:
            self._load_csv_schedule(**kwargs)
        elif mode == ScheduleMode.API:
            self._setup_api_mode(**kwargs)
        
        # Notify callback
        if self._on_schedule_update:
            self._on_schedule_update()
    
    def _generate_random_schedule(
        self,
        start_time: datetime = None,
        duration_h: float = 0.5,
        min_power: float = -1000.0,
        max_power: float = 1000.0,
        q_power: float = 0.0,
        resolution_min: int = 5
    ):
        """
        Generate a random schedule (Mode 1).
        
        Args:
            start_time: Start time for the schedule (default: now)
            duration_h: Duration in hours
            min_power: Minimum power (kW)
            max_power: Maximum power (kW)
            q_power: Reactive power setpoint (kvar)
            resolution_min: Time resolution in minutes
        """
        if start_time is None:
            start_time = datetime.now().replace(microsecond=0)
        
        logging.info(f"ScheduleManager: Generating random schedule from {start_time} for {duration_h}h")
        
        # Generate timestamps at specified resolution
        num_periods = int(duration_h * 60 / resolution_min) + 1
        timestamps = pd.date_range(start=start_time, periods=num_periods, freq=f'{resolution_min}min')
        
        # Generate random power setpoints
        power_setpoints = np.random.uniform(min_power, max_power, size=len(timestamps))
        
        # Create DataFrame
        df = pd.DataFrame({
            'power_setpoint_kw': power_setpoints,
            'reactive_power_setpoint_kvar': q_power
        }, index=timestamps)
        
        # Ensure last setpoint is zero for predictable end state
        df.iloc[-1] = 0
        
        with self._lock:
            self._schedule_df = df
        
        logging.info(f"ScheduleManager: Generated {len(df)} setpoints at {resolution_min}-min resolution")
    
    def _load_csv_schedule(self, csv_path: str, start_time: datetime = None, q_power: float = 0.0):
        """
        Load a schedule from a CSV file (Mode 2).
        
        Args:
            csv_path: Path to the CSV file
            start_time: Start time to use (if None, uses the first timestamp in CSV)
            q_power: Default reactive power if not in CSV
        """
        csv_file = Path(csv_path)
        if not csv_file.exists():
            raise FileNotFoundError(f"Schedule file not found: {csv_path}")
        
        logging.info(f"ScheduleManager: Loading schedule from {csv_path}")
        
        # Read CSV
        df = pd.read_csv(csv_path, parse_dates=['datetime'])
        
        # Ensure required columns exist
        if 'power_setpoint_kw' not in df.columns:
            raise ValueError("CSV must contain 'power_setpoint_kw' column")
        
        if 'reactive_power_setpoint_kvar' not in df.columns:
            df['reactive_power_setpoint_kvar'] = q_power
        
        # Handle start_time offset
        if start_time is not None:
            if 'original_datetime' not in df.columns:
                df['original_datetime'] = df['datetime']
            
            # Calculate the offset from the first timestamp in the file
            first_ts = df['datetime'].iloc[0]
            offset = start_time - first_ts
            
            # Add offset to all timestamps
            df['datetime'] = df['datetime'] + offset
        
        # Set datetime as index
        df = df.set_index('datetime')
        
        with self._lock:
            self._schedule_df = df
        
        logging.info(f"ScheduleManager: Loaded {len(df)} setpoints from CSV")
    
    def _setup_api_mode(self, api_password: str = None):
        """
        Set up API mode and fetch current day schedule (Mode 3).
        
        Args:
            api_password: The API password (required)
        """
        if not api_password:
            raise ValueError("API password is required for API mode")
        
        logging.info("ScheduleManager: Setting up API mode")
        
        # Initialize API
        self._api = IstentoreAPI()
        self._api.set_password(api_password)
        
        # Fetch current day schedule immediately
        self._fetch_current_day_schedule()
        
        # Start polling for next day schedule
        self._start_api_polling()
    
    def _fetch_current_day_schedule(self):
        """Fetch the current day's schedule from the API."""
        if not self._api:
            logging.error("ScheduleManager: API not initialized")
            return
        
        try:
            now = datetime.now()
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            today_end = today_start + timedelta(days=1) - timedelta(minutes=15)
            
            schedule = self._api.get_day_ahead_schedule(today_start, today_end)
            
            if schedule:
                df = self._api.schedule_to_dataframe(schedule)
                self._append_to_schedule(df)
                logging.info(f"ScheduleManager: Fetched current day schedule ({len(df)} points)")
            else:
                logging.warning("ScheduleManager: No current day schedule available from API")
        
        except Exception as e:
            logging.error(f"ScheduleManager: Failed to fetch current day schedule: {e}")
    
    def _fetch_next_day_schedule(self) -> bool:
        """
        Fetch the next day's schedule from the API.
        
        Returns:
            True if successful, False otherwise
        """
        if not self._api:
            logging.error("ScheduleManager: API not initialized")
            return False
        
        try:
            now = datetime.now()
            next_day_start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            next_day_end = next_day_start + timedelta(days=1) - timedelta(minutes=15)
            
            schedule = self._api.get_day_ahead_schedule(next_day_start, next_day_end)
            
            if schedule:
                df = self._api.schedule_to_dataframe(schedule)
                self._append_to_schedule(df)
                logging.info(f"ScheduleManager: Fetched next day schedule ({len(df)} points)")
                return True
            else:
                logging.info("ScheduleManager: Next day schedule not yet available from API")
                return False
        
        except Exception as e:
            logging.error(f"ScheduleManager: Failed to fetch next day schedule: {e}")
            return False
    
    def _start_api_polling(self):
        """Start the background thread for polling next-day schedules."""
        def polling_loop():
            logging.info(f"ScheduleManager: Starting API polling (start at {self._poll_start_time}, interval {self._poll_interval_min}min)")
            
            # Parse the start poll time
            poll_start_hour, poll_start_minute = map(int, self._poll_start_time.split(':'))
            
            while not self._shutdown_event.is_set():
                now = datetime.now()
                current_time = now.strftime("%H:%M")
                
                # Check if it's time to start polling
                if current_time >= self._poll_start_time:
                    success = self._fetch_next_day_schedule()
                    if success:
                        logging.info("ScheduleManager: Next day schedule fetched, stopping polling")
                        break
                
                # Wait for poll interval
                poll_seconds = self._poll_interval_min * 60
                self._shutdown_event.wait(timeout=poll_seconds)
            
            logging.info("ScheduleManager: API polling thread stopped")
        
        self._api_polling_thread = threading.Thread(target=polling_loop, daemon=True)
        self._api_polling_thread.start()
    
    def _append_to_schedule(self, new_data: pd.DataFrame, replace_overlapping: bool = True):
        """
        Append new schedule data, replacing only overlapping periods.
        
        Args:
            new_data: DataFrame with new schedule data
            replace_overlapping: If True, replace existing data for overlapping periods
        """
        with self._lock:
            if self._schedule_df.empty:
                self._schedule_df = new_data.copy()
            else:
                if replace_overlapping:
                    self._schedule_df = merge_schedule_frames(self._schedule_df, new_data)
                else:
                    self._schedule_df = pd.concat([self._schedule_df, new_data]).sort_index()
        
        # Notify callback
        if self._on_schedule_update:
            self._on_schedule_update()
    
    def append_schedule_from_dict(self, schedule_dict: Dict[str, float], default_q_kvar: float = 0.0):
        """
        Append schedule data from a dictionary.
        
        Args:
            schedule_dict: Dictionary with ISO datetime keys and power values
            default_q_kvar: Default reactive power value
        """
        if not schedule_dict:
            return
        
        if not self._api:
            # Create DataFrame without API conversion
            data = []
            for dt_str, power_kw in schedule_dict.items():
                if '+' in dt_str or 'Z' in dt_str:
                    dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
                else:
                    dt = datetime.fromisoformat(dt_str)
                    dt = dt.replace(tzinfo=None)  # Naive datetime for index
                
                data.append({
                    'datetime': dt,
                    'power_setpoint_kw': power_kw,
                    'reactive_power_setpoint_kvar': default_q_kvar
                })
            
            df = pd.DataFrame(data).set_index('datetime')
            self._append_to_schedule(df)
        else:
            # Use API conversion
            df = self._api.schedule_to_dataframe(schedule_dict, default_q_kvar)
            self._append_to_schedule(df)
    
    def get_current_setpoint(self, current_time: datetime = None) -> tuple:
        """
        Get the current setpoint for the given time.
        
        Uses asof() to find the value just before the current time.
        
        Args:
            current_time: The current time (default: now)
            
        Returns:
            Tuple of (power_kw, reactive_power_kvar) or (0.0, 0.0) if no data
        """
        if current_time is None:
            current_time = datetime.now()
        
        with self._lock:
            if self._schedule_df.empty:
                return 0.0, 0.0
            
            # Use asof to find the value just before current time
            row = self._schedule_df.asof(current_time)
            
            if pd.isna(row).all():
                return 0.0, 0.0
            
            power = row.get('power_setpoint_kw', 0.0)
            q_power = row.get('reactive_power_setpoint_kvar', 0.0)
            
            return power, q_power
    
    def to_csv(self, filepath: str):
        """
        Export the current schedule to a CSV file.
        
        Args:
            filepath: Path to save the CSV file
        """
        with self._lock:
            if self._schedule_df.empty:
                logging.warning("ScheduleManager: Cannot export empty schedule")
                return
            
            df = self._schedule_df.reset_index()
            df.to_csv(filepath, index=False, float_format='%.2f')
            logging.info(f"ScheduleManager: Schedule exported to {filepath}")


# Backward compatibility function for data_fetcher_agent
def create_schedule_csv(config: dict, csv_path: str = None):
    """
    Create a random schedule CSV file (backward compatibility function).
    
    Args:
        config: Configuration dictionary
        csv_path: Path to save the CSV file (default: from config)
    """
    if csv_path is None:
        csv_path = config.get('SCHEDULE_SOURCE_CSV', 'schedule_source.csv')
    
    start_time = config.get('SCHEDULE_START_TIME', datetime.now().replace(microsecond=0))
    duration_h = config.get('SCHEDULE_DURATION_H', 0.5)
    # Use plant power limits for schedule generation
    min_power = config.get('PLANT_P_MIN_KW', -1000)
    max_power = config.get('PLANT_P_MAX_KW', 1000)
    q_min = config.get('PLANT_Q_MIN_KVAR', -600)
    q_max = config.get('PLANT_Q_MAX_KVAR', 600)
    resolution_min = config.get('SCHEDULE_DEFAULT_RESOLUTION_MIN', 5)
    
    logging.info(f"Creating schedule file: {csv_path}")
    
    # Generate timestamps
    num_periods = int(duration_h * 60 / resolution_min) + 1
    timestamps = pd.date_range(start=start_time, periods=num_periods, freq=f'{resolution_min}min')
    
    # Generate random power setpoints
    power_setpoints = np.random.uniform(min_power, max_power, size=len(timestamps))
    
    # Generate random reactive power setpoints
    reactive_setpoints = np.random.uniform(q_min, q_max, size=len(timestamps))
    
    # Create DataFrame
    df = pd.DataFrame({
        'datetime': timestamps,
        'power_setpoint_kw': power_setpoints,
        'reactive_power_setpoint_kvar': reactive_setpoints
    })
    
    # Ensure last setpoint is zero
    df.iloc[-1, 1:] = 0
    
    # Save to CSV
    df.to_csv(csv_path, index=False, float_format='%.2f')
    logging.info("Schedule file created.")


if __name__ == "__main__":
    # Test the schedule manager
    logging.basicConfig(level=logging.INFO)
    
    config = {
        'ISTENTORE_POLL_INTERVAL_MIN': 10,
        'ISTENTORE_POLL_START_TIME': '17:30',
    }
    
    manager = ScheduleManager(config)
    
    # Test Mode 1: Random schedule
    print("\n=== Testing Mode 1: Random Schedule ===")
    manager.set_mode(
        ScheduleMode.RANDOM,
        start_time=datetime.now(),
        duration_h=1.0,
        min_power=-500,
        max_power=500,
        resolution_min=5
    )
    
    df = manager.schedule_df
    print(f"Schedule shape: {df.shape}")
    print(f"Start time: {manager.start_time}")
    print(f"End time: {manager.end_time}")
    print(df.head())
    
    # Test current setpoint
    power, q_power = manager.get_current_setpoint()
    print(f"Current setpoint: P={power:.2f} kW, Q={q_power:.2f} kvar")
    
    # Clear and test Mode 2
    print("\n=== Testing Mode 2: CSV (using random as test) ===")
    manager.set_mode(
        ScheduleMode.RANDOM,
        start_time=datetime.now(),
        duration_h=0.5,
        min_power=100,
        max_power=200,
        resolution_min=5
    )
    df = manager.schedule_df
    print(f"Schedule shape: {df.shape}")
    print(df.head())
    
    print("\n=== Schedule Manager Test Complete ===")
