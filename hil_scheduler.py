import logging
import pandas as pd
import threading
import time
from datetime import datetime, timedelta

from config import configure_scheduler
from scheduler_agent import scheduler_agent
from data_fetcher_agent import data_fetcher_agent
from ppc_agent import ppc_agent
from battery_agent import battery_agent
from measurement_agent import measurement_agent
from dashboard_agent import dashboard_agent

# --- CONFIGURATION ---
REMOTE_PLANT = True # False assumes external ppc & battery, True activates local ppc & battery emulation
REMOTE_DATA = False # False fetches the data from a local file, true fetches data from remote server

def main():
    """
    Director Agent: Sets up and runs all other agents.
    """

    # --- Configuration ---
    config = configure_scheduler(remote_plant=REMOTE_PLANT, remote_data=REMOTE_DATA)
    logging.basicConfig(level=config["LOG_LEVEL"], format='%(asctime)s - %(levelname)s - %(message)s')
    logging.info("Director agent starting the application.")
    end_time = config["SCHEDULE_START_TIME"] + timedelta(hours=config["SCHEDULE_DURATION_H"])    

    # --- Create shared data ---
    shared_data = {
        # Dataframe that holds the power schedule received from control center
        "schedule_final_df": pd.DataFrame(),
        # Dataframe that holds the measurements
        "measurements_df": pd.DataFrame(),
        # Lock for shared data
        "lock": threading.Lock(),
        # Event to signal shutdown
        "shutdown_event": threading.Event(),
    }

    try:
        # --- Create and start agent threads and modbus servers ---
        threads = [
                threading.Thread(target=data_fetcher_agent, args=(config, shared_data)),
                threading.Thread(target=scheduler_agent, args=(config, shared_data)),
                threading.Thread(target=measurement_agent, args=(config, shared_data)),
                threading.Thread(target=dashboard_agent, args=(config, shared_data))
            ]
        if not REMOTE_PLANT:
            # Local plant emulation threads
            threads.append( threading.Thread(target=ppc_agent, args=(config, shared_data)) )
            threads.append( threading.Thread(target=battery_agent, args=(config, shared_data)) )

        for t in threads:
            t.start()

        logging.info(f"All agents started. Running until {end_time}.")

        # Wait until the schedule is over
        while datetime.now() < end_time:
            time.sleep(1)
            if shared_data["shutdown_event"].is_set(): # Allow for early exit
                break

    except KeyboardInterrupt:
        logging.info("Keyboard interrupt received. Shutting down...")
    except Exception as e:
        logging.error(f"An unexpected error occurred in the director: {e}")
    finally:
        # --- Shutdown sequence ---
        logging.info("Director initiating shutdown...")
        shared_data["shutdown_event"].set()

        logging.info("Waiting for agent threads to finish...")
        for t in threads:
            t.join()
        logging.info("All agent threads have finished.")

        logging.info("Application shutdown complete.")


if __name__ == "__main__":
    main()