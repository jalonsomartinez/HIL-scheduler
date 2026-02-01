import logging
import time
import pandas as pd
from datetime import datetime, timedelta

from istentore_api import IstentoreAPI, AuthenticationError


def data_fetcher_agent(config, shared_data):
    """
    Data fetcher agent that fetches schedules from the Istentore API.
    
    This agent runs independently in a loop:
    1. Waits for an API password to be set in shared_data
    2. Once password is set, connects to the API
    3. Fetches today's schedule
    4. Polls for tomorrow's schedule starting at configured time
    5. Updates the shared api_schedule_df with fetched data
    6. Updates data_fetcher_status for dashboard display
    
    The agent is completely decoupled from the dashboard and scheduler.
    It only reads api_password and writes to api_schedule_df and data_fetcher_status.
    """
    logging.info("Data fetcher agent started.")
    
    api = None
    password_checked = False
    poll_start_time = config.get('ISTENTORE_POLL_START_TIME', '17:30')
    poll_interval_min = config.get('ISTENTORE_POLL_INTERVAL_MIN', 10)
    first_fetch_done = False  # Track if first fetch was successful
    
    while not shared_data['shutdown_event'].is_set():
        try:
            # Check if password is set
            with shared_data['lock']:
                password = shared_data.get('api_password')
            
            if not password:
                # No password set, just wait
                if password_checked:
                    logging.info("Data fetcher: Password cleared, resetting connection.")
                    api = None
                    password_checked = False
                    _update_status(shared_data, connected=False)
                time.sleep(5)
                continue
            
            password_checked = True
            
            # Initialize API if needed
            if api is None:
                api = IstentoreAPI()
                api.set_password(password)
                logging.info("Data fetcher: API initialized with password.")
            
            # Check if password has changed
            if api._password != password:
                api.set_password(password)
                logging.info("Data fetcher: Password updated.")
            
            # Fetch today's schedule if not already fetched
            with shared_data['lock']:
                status = shared_data.get('data_fetcher_status', {})
                today_fetched = status.get('today_fetched', False)
            
            if not today_fetched:
                logging.info("Data fetcher: Fetching today's schedule...")
                try:
                    now = datetime.now()
                    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                    today_end = today_start + timedelta(days=1) - timedelta(minutes=15)
                    
                    schedule = api.get_day_ahead_schedule(today_start, today_end)
                    
                    if schedule:
                        df = api.schedule_to_dataframe(schedule)
                        with shared_data['lock']:
                            shared_data['api_schedule_df'] = df
                        _update_status(shared_data,
                            connected=True,
                            today_fetched=True,
                            today_points=len(df),
                            error=None
                        )
                        first_fetch_done = True  # Mark first fetch as done
                        logging.info(f"Data fetcher: Today's schedule fetched ({len(df)} points).")
                    else:
                        _update_status(shared_data, 
                            connected=True, 
                            today_fetched=False,
                            error="No data available for today"
                        )
                        logging.warning("Data fetcher: No schedule available for today.")
                
                except AuthenticationError as e:
                    _update_status(shared_data, connected=False, error=f"Authentication failed: {e}")
                    logging.error(f"Data fetcher: Authentication failed: {e}")
                    api = None
                    time.sleep(30)  # Wait longer on auth error
                    continue
                except Exception as e:
                    _update_status(shared_data, error=str(e))
                    logging.error(f"Data fetcher: Error fetching today's schedule: {e}")
            
            # Check if it's time to poll for tomorrow's schedule
            now = datetime.now()
            current_time = now.strftime("%H:%M")
            
            with shared_data['lock']:
                status = shared_data.get('data_fetcher_status', {})
                tomorrow_fetched = status.get('tomorrow_fetched', False)
            
            if not tomorrow_fetched and current_time >= poll_start_time:
                logging.info("Data fetcher: Polling for tomorrow's schedule...")
                try:
                    tomorrow_start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                    tomorrow_end = tomorrow_start + timedelta(days=1) - timedelta(minutes=15)
                    
                    schedule = api.get_day_ahead_schedule(tomorrow_start, tomorrow_end)
                    
                    if schedule:
                        df = api.schedule_to_dataframe(schedule)
                        
                        # Get existing schedule reference (brief lock)
                        with shared_data['lock']:
                            existing_df = shared_data['api_schedule_df']
                        
                        # DataFrame operations outside lock
                        if not existing_df.empty:
                            # Remove overlapping periods
                            non_overlapping = existing_df.index.difference(df.index)
                            existing_df = existing_df.loc[non_overlapping]
                            combined_df = pd.concat([existing_df, df]).sort_index()
                        else:
                            combined_df = df
                        
                        # Brief lock only for assignment
                        with shared_data['lock']:
                            shared_data['api_schedule_df'] = combined_df
                        
                        _update_status(shared_data, 
                            connected=True,
                            tomorrow_fetched=True,
                            tomorrow_points=len(df),
                            error=None
                        )
                        logging.info(f"Data fetcher: Tomorrow's schedule fetched ({len(df)} points).")
                    else:
                        logging.info("Data fetcher: Tomorrow's schedule not yet available.")
                
                except Exception as e:
                    logging.error(f"Data fetcher: Error fetching tomorrow's schedule: {e}")
            
            # Update last attempt timestamp
            _update_status(shared_data, last_attempt=now.isoformat())

            # Sleep until next check - use 5 minutes after first fetch, otherwise 60 seconds
            if first_fetch_done:
                sleep_time = 300  # 5 minutes
            else:
                sleep_time = config.get("DATA_FETCHER_PERIOD_S", 60)
            time.sleep(sleep_time)
            
        except Exception as e:
            logging.error(f"Data fetcher: Unexpected error: {e}")
            time.sleep(5)
    
    logging.info("Data fetcher agent stopped.")


def _update_status(shared_data, **kwargs):
    """Update the data_fetcher_status in shared_data."""
    with shared_data['lock']:
        if 'data_fetcher_status' not in shared_data:
            shared_data['data_fetcher_status'] = {}
        shared_data['data_fetcher_status'].update(kwargs)


if __name__ == "__main__":
    # Test the data fetcher agent
    import threading
    
    # Create a mock config
    config = {
        'DATA_FETCHER_PERIOD_S': 5,
        'ISTENTORE_POLL_INTERVAL_MIN': 1,
        'ISTENTORE_POLL_START_TIME': '00:00',
    }
    
    # Create shared data
    shared_data = {
        'lock': threading.Lock(),
        'shutdown_event': threading.Event(),
        'api_schedule_df': pd.DataFrame(),
        'api_password': None,
        'data_fetcher_status': {},
    }
    
    # Test the agent in a separate thread
    def run_agent():
        data_fetcher_agent(config, shared_data)
    
    agent_thread = threading.Thread(target=run_agent, daemon=True)
    agent_thread.start()
    
    # Wait a moment
    time.sleep(1)
    
    print("Data fetcher running. Setting password...")
    with shared_data['lock']:
        shared_data['api_password'] = 'test_password'
    
    # Wait a bit
    time.sleep(3)
    
    # Check status
    with shared_data['lock']:
        status = shared_data.get('data_fetcher_status', {})
        print(f"Status: {status}")
    
    # Stop the agent
    shared_data['shutdown_event'].set()
    agent_thread.join(timeout=2)
    
    print("Data fetcher agent test complete.")
