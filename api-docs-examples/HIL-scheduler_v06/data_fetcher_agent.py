import logging
import time
import numpy as np
import pandas as pd
from datetime import timedelta


def create_schedule_csv(config):
    """
    Creates a power schedule by generating random 5-minute setpoints and
    interpolating them to a 1-minute resolution.
    """
    logging.info(f"Creating schedule file: {config['SCHEDULE_SOURCE_CSV']}")

    start_time= config["SCHEDULE_START_TIME"]
    duration_h = config["SCHEDULE_DURATION_H"]
    min_power = config["SCHEDULE_POWER_MIN_KW"]
    max_power = config["SCHEDULE_POWER_MAX_KW"]

    # 1. Generate a coarse schedule with 5-minute intervals.
    # We need enough points to cover the full duration.
    num_periods_5min = int(duration_h * 60 / 5) + 1
    timestamps_5min = pd.date_range(start=start_time, periods=num_periods_5min, freq='5min')

    power_setpoints_5min = np.random.uniform(
        min_power,
        max_power,
        size=len(timestamps_5min)
    )

    coarse_schedule_df = pd.DataFrame({
        'datetime': timestamps_5min,
        'power_setpoint_kw': power_setpoints_5min
    }).set_index('datetime')

    # 2. Create a fine-grained 1-minute index for the exact duration.
    num_periods_1min = int(duration_h * 60)
    fine_index = pd.date_range(start=start_time, periods=num_periods_1min, freq='1min')

    # 3. Resample and interpolate.
    # Reindex to the union of both indexes, interpolate, then select just the fine index.
    combined_index = coarse_schedule_df.index.union(fine_index)
    resampled_df = coarse_schedule_df.reindex(combined_index).interpolate(method='time')
    final_schedule_df = resampled_df.loc[fine_index]

    # 4. Ensure the last setpoint is zero for a predictable end state.
    final_schedule_df.iloc[-1] = 0

    # 5. Save to CSV.
    final_schedule_df.reset_index().rename(columns={'index': 'datetime'}).to_csv(
        config['SCHEDULE_SOURCE_CSV'], index=False, float_format='%.2f'
    )
    logging.info("Schedule file created.")


def data_fetcher_agent(config, shared_data):
    """
    Periodically reads the source schedule CSV and creates a 1-second resolution
    schedule for other agents to use.
    """
    logging.info("Data fetcher agent started.")
  
    create_schedule_csv(config)
    
    while not shared_data['shutdown_event'].is_set():
        try:
            source_df = pd.read_csv(config['SCHEDULE_SOURCE_CSV'], parse_dates=['datetime'])
            source_df = source_df.set_index('datetime')
            
            # Create a 1-second time index for the schedule duration
            start_time = source_df.index[0]
            end_time = start_time + timedelta(hours=config["SCHEDULE_DURATION_H"])
            full_day_index = pd.date_range(start=start_time, end=end_time, freq='s')
            
            # Resample schedule to 1-second resolution using forward fill
            upsampled_df = source_df.reindex(full_day_index, method='ffill')
            
            with shared_data['lock']:
                shared_data['schedule_final_df'] = upsampled_df

            time.sleep(config["DATA_FETCHER_PERIOD_S"])

        except FileNotFoundError:
            logging.warning(f"Schedule file {config['SCHEDULE_SOURCE_CSV']} not found. Waiting...")
            time.sleep(5)
        except Exception as e:
            logging.error(f"Error in data fetcher agent: {e}")
            time.sleep(5)

    logging.info("Data fetcher agent stopped.")
