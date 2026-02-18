import logging
import time
import pandas as pd
from datetime import timedelta

from istentore_api import IstentoreAPI, AuthenticationError
from time_utils import now_tz


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

    Timing Strategy:
    - Uses DATA_FETCHER_PERIOD_S from config for normal polling (default: 120s)
    - Uses hardcoded 30s backoff for all error conditions
    """
    logging.info("Data fetcher agent started.")

    api = None
    password_checked = False
    poll_start_time = config.get('ISTENTORE_POLL_START_TIME', '17:30')
    poll_interval_s = config.get('DATA_FETCHER_PERIOD_S', 120)
    error_backoff_s = 30  # Single backoff for all errors

    logging.info(f"Data fetcher: poll_interval={poll_interval_s}s, error_backoff={error_backoff_s}s, poll_start_time={poll_start_time}")
    
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
                time.sleep(error_backoff_s)
                continue
            
            password_checked = True
            
            # Initialize API if needed
            if api is None:
                api = IstentoreAPI(timezone_name=config.get("TIMEZONE_NAME"))
                api.set_password(password)
                logging.info("Data fetcher: API initialized with password.")
            
            # Check if password has changed
            if api._password != password:
                api.set_password(password)
                logging.info("Data fetcher: Password updated.")

            now = now_tz(config)
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            today_end = today_start + timedelta(days=1) - timedelta(minutes=15)
            tomorrow_start = today_start + timedelta(days=1)
            tomorrow_end = tomorrow_start + timedelta(days=1) - timedelta(minutes=15)
            today_date = today_start.date().isoformat()
            tomorrow_date = tomorrow_start.date().isoformat()

            _reconcile_day_status(shared_data, today_date, tomorrow_date)

            with shared_data['lock']:
                status = shared_data.get('data_fetcher_status', {}).copy()
                today_fetched = status.get('today_fetched', False)
                tomorrow_fetched = status.get('tomorrow_fetched', False)

            if not today_fetched:
                logging.info(f"Data fetcher: Fetching today's schedule ({today_date})...")
                try:
                    schedule = api.get_day_ahead_schedule(today_start, today_end)

                    if schedule:
                        df = api.schedule_to_dataframe(schedule)
                        with shared_data['lock']:
                            shared_data['api_schedule_df'] = df
                        _update_status(
                            shared_data,
                            connected=True,
                            today_fetched=True,
                            today_date=today_date,
                            today_points=len(df),
                            error=None,
                        )
                        logging.info(f"Data fetcher: Today's schedule fetched ({today_date}, {len(df)} points).")
                    else:
                        _update_status(
                            shared_data,
                            connected=True,
                            today_fetched=False,
                            today_date=today_date,
                            today_points=0,
                            error="No data available for today",
                        )
                        logging.warning(f"Data fetcher: No schedule available for today ({today_date}).")

                except AuthenticationError as e:
                    _update_status(shared_data, connected=False, error=f"Authentication failed: {e}")
                    logging.error(f"Data fetcher: Authentication failed: {e}")
                    api = None
                    time.sleep(error_backoff_s)
                    continue
                except Exception as e:
                    _update_status(shared_data, error=str(e))
                    logging.error(f"Data fetcher: Error fetching today's schedule: {e}")

            current_time = now.strftime("%H:%M")
            if not tomorrow_fetched and current_time >= poll_start_time:
                logging.info(f"Data fetcher: Polling tomorrow's schedule ({tomorrow_date})...")
                try:
                    schedule = api.get_day_ahead_schedule(tomorrow_start, tomorrow_end)

                    if schedule:
                        df = api.schedule_to_dataframe(schedule)

                        with shared_data['lock']:
                            existing_df = shared_data['api_schedule_df']

                        if not existing_df.empty:
                            non_overlapping = existing_df.index.difference(df.index)
                            existing_df = existing_df.loc[non_overlapping]
                            combined_df = pd.concat([existing_df, df]).sort_index()
                        else:
                            combined_df = df

                        with shared_data['lock']:
                            shared_data['api_schedule_df'] = combined_df

                        _update_status(
                            shared_data,
                            connected=True,
                            tomorrow_fetched=True,
                            tomorrow_date=tomorrow_date,
                            tomorrow_points=len(df),
                            error=None,
                        )
                        logging.info(f"Data fetcher: Tomorrow's schedule fetched ({tomorrow_date}, {len(df)} points).")
                    else:
                        logging.info(f"Data fetcher: Tomorrow's schedule not yet available ({tomorrow_date}).")

                except AuthenticationError as e:
                    _update_status(shared_data, connected=False, error=f"Authentication failed: {e}")
                    logging.error(f"Data fetcher: Authentication failed: {e}")
                    api = None
                    time.sleep(error_backoff_s)
                    continue
                except Exception as e:
                    _update_status(shared_data, error=str(e))
                    logging.error(f"Data fetcher: Error fetching tomorrow's schedule: {e}")

            _update_status(shared_data, last_attempt=now.isoformat())

            # Sleep until next check
            time.sleep(poll_interval_s)
            
        except Exception as e:
            logging.error(f"Data fetcher: Unexpected error: {e}")
            time.sleep(error_backoff_s)
    
    logging.info("Data fetcher agent stopped.")


def _update_status(shared_data, **kwargs):
    """Update the data_fetcher_status in shared_data."""
    with shared_data['lock']:
        if 'data_fetcher_status' not in shared_data:
            shared_data['data_fetcher_status'] = {}
        shared_data['data_fetcher_status'].update(kwargs)


def _reconcile_day_status(shared_data, today_date, tomorrow_date):
    """
    Keep day-scoped fetch flags aligned with current local dates.

    On day rollover:
    - Promote yesterday's fetched "tomorrow" to new "today" when dates align.
    - Reset stale tomorrow flags/points for the newly calculated tomorrow date.
    """
    with shared_data['lock']:
        status = shared_data.get('data_fetcher_status', {}).copy()

    previous_today_date = status.get('today_date')
    previous_tomorrow_date = status.get('tomorrow_date')
    previous_tomorrow_fetched = status.get('tomorrow_fetched', False)
    previous_tomorrow_points = status.get('tomorrow_points', 0)

    updates = {}
    if previous_today_date != today_date:
        can_promote_tomorrow = previous_tomorrow_fetched and previous_tomorrow_date == today_date
        if can_promote_tomorrow:
            updates['today_fetched'] = True
            updates['today_points'] = previous_tomorrow_points
            logging.info(
                f"Data fetcher: Day rollover detected. Promoted fetched tomorrow ({today_date}) to today."
            )
        else:
            updates['today_fetched'] = False
            updates['today_points'] = 0
            logging.info(
                f"Data fetcher: Day rollover detected. Reset today's fetch state for {today_date}."
            )

    if previous_tomorrow_date != tomorrow_date:
        updates['tomorrow_fetched'] = False
        updates['tomorrow_points'] = 0

    if previous_today_date != today_date:
        updates['today_date'] = today_date
    if previous_tomorrow_date != tomorrow_date:
        updates['tomorrow_date'] = tomorrow_date

    if updates:
        _update_status(shared_data, **updates)


if __name__ == "__main__":
    # Test the data fetcher agent
    import threading
    
    # Create a mock config
    config = {
        'DATA_FETCHER_PERIOD_S': 5,
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
