import logging
import time
import threading
import pandas as pd
from datetime import timedelta

from schedule_manager import ScheduleManager, ScheduleMode


def data_fetcher_agent(config, shared_data):
    """
    Data fetcher agent that manages the schedule and keeps it updated.
    
    This agent uses the ScheduleManager to handle all three schedule modes:
    1. Random schedule generation
    2. CSV file loading
    3. API fetching with polling
    
    The agent periodically updates the shared schedule DataFrame and handles
    mode changes triggered from the dashboard.
    """
    logging.info("Data fetcher agent started.")
    
    # Initialize the schedule manager
    schedule_manager = ScheduleManager(config)
    
    # Store schedule manager in shared data for dashboard access
    shared_data['schedule_manager'] = schedule_manager
    
    # Set up callback for schedule updates
    def on_schedule_update():
        """Called when the schedule is updated."""
        with shared_data['lock']:
            shared_data['schedule_final_df'] = schedule_manager.schedule_df
    
    schedule_manager.set_on_schedule_update_callback(on_schedule_update)
    
    # Check if there's a mode set in shared_data (from dashboard)
    def check_for_mode_changes():
        """Check for mode changes from the dashboard."""
        if 'schedule_mode' in shared_data:
            mode = shared_data.pop('schedule_mode', None)
            mode_params = shared_data.pop('schedule_mode_params', {})
            
            if mode == 'random':
                logging.info("Data fetcher: Applying random mode from dashboard")
                schedule_manager.set_mode(ScheduleMode.RANDOM, **mode_params)
            elif mode == 'csv':
                logging.info("Data fetcher: Applying CSV mode from dashboard")
                schedule_manager.set_mode(ScheduleMode.CSV, **mode_params)
            elif mode == 'api':
                logging.info("Data fetcher: Applying API mode from dashboard")
                schedule_manager.set_mode(ScheduleMode.API, **mode_params)
    
    # Start with empty schedule (user must select mode via dashboard)
    # The schedule will be populated when user selects a mode
    
    while not shared_data['shutdown_event'].is_set():
        try:
            # Check for mode changes from dashboard
            check_for_mode_changes()
            
            # Update the shared schedule DataFrame
            with shared_data['lock']:
                shared_data['schedule_final_df'] = schedule_manager.schedule_df
            
            # Sleep for the configured period
            time.sleep(config["DATA_FETCHER_PERIOD_S"])
            
        except Exception as e:
            logging.error(f"Error in data fetcher agent: {e}")
            time.sleep(5)
    
    logging.info("Data fetcher agent stopped.")


def create_schedule_csv(config):
    """
    Creates a power schedule by generating random setpoints at the specified resolution.
    
    This function is kept for backward compatibility but now generates at the
    configured resolution (default 5 minutes) instead of upsampling.
    
    Args:
        config: Configuration dictionary with schedule settings
    """
    logging.info(f"Creating schedule file: {config['SCHEDULE_SOURCE_CSV']}")

    from schedule_manager import create_schedule_csv as sm_create_csv
    sm_create_csv(config)


if __name__ == "__main__":
    # Test the data fetcher agent
    import pandas as pd
    from datetime import datetime
    
    # Create a mock config
    config = {
        'SCHEDULE_SOURCE_CSV': 'test_schedule.csv',
        'SCHEDULE_START_TIME': datetime.now().replace(microsecond=0),
        'SCHEDULE_DURATION_H': 0.5,
        'SCHEDULE_POWER_MIN_KW': -1000,
        'SCHEDULE_POWER_MAX_KW': 1000,
        'SCHEDULE_Q_MIN_KVAR': -600,
        'SCHEDULE_Q_MAX_KVAR': 600,
        'DATA_FETCHER_PERIOD_S': 1,
        'ISTENTORE_POLL_INTERVAL_MIN': 10,
        'ISTENTORE_POLL_START_TIME': '17:30',
        'SCHEDULE_DEFAULT_MIN_POWER_KW': -1000,
        'SCHEDULE_DEFAULT_MAX_POWER_KW': 1000,
        'SCHEDULE_DEFAULT_Q_POWER_KVAR': 0,
        'SCHEDULE_DEFAULT_RESOLUTION_MIN': 5,
    }
    
    # Create shared data
    shared_data = {
        'lock': threading.Lock(),
        'shutdown_event': threading.Event(),
        'schedule_final_df': pd.DataFrame(),
    }
    
    # Test the agent in a separate thread
    import threading
    
    def run_agent():
        data_fetcher_agent(config, shared_data)
    
    agent_thread = threading.Thread(target=run_agent, daemon=True)
    agent_thread.start()
    
    # Wait a moment
    time.sleep(0.5)
    
    # Check the schedule manager
    if 'schedule_manager' in shared_data:
        sm = shared_data['schedule_manager']
        print(f"Schedule manager mode: {sm.mode}")
        print(f"Schedule empty: {sm.is_empty}")
        
        # Generate a random schedule
        shared_data['schedule_mode'] = 'random'
        shared_data['schedule_mode_params'] = {
            'start_time': datetime.now(),
            'duration_h': 1.0,
            'min_power': -500,
            'max_power': 500,
            'resolution_min': 5
        }
        
        time.sleep(1)
        
        df = sm.schedule_df
        print(f"Schedule shape: {df.shape}")
        print(f"Start: {sm.start_time}")
        print(f"End: {sm.end_time}")
        print(df.head())
    
    # Stop the agent
    shared_data['shutdown_event'].set()
    agent_thread.join(timeout=2)
    
    print("Data fetcher agent test complete.")
