import logging
import pandas as pd
import threading
import time
from datetime import datetime, timedelta

from config_loader import load_config
from scheduler_agent import scheduler_agent
from data_fetcher_agent import data_fetcher_agent
from plant_agent import plant_agent
from measurement_agent import measurement_agent
from dashboard_agent import dashboard_agent


def main():
    """
    Director Agent: Sets up and runs all other agents.
    """
    
    # --- Configuration ---
    config = load_config("config.yaml")
    logging.basicConfig(
        level=config["LOG_LEVEL"],
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    logging.info("Director agent starting the application.")
    
    # --- Create shared data ---
    # Get startup values from config, with defaults
    startup_schedule_source = config.get("STARTUP_SCHEDULE_SOURCE", "manual")
    startup_plant = config.get("STARTUP_PLANT", "local")
    
    # Validate startup values
    if startup_schedule_source not in ["manual", "api"]:
        logging.warning(f"Invalid STARTUP_SCHEDULE_SOURCE '{startup_schedule_source}', using 'manual'")
        startup_schedule_source = "manual"
    if startup_plant not in ["local", "remote"]:
        logging.warning(f"Invalid STARTUP_PLANT '{startup_plant}', using 'local'")
        startup_plant = "local"
    
    logging.info(f"Startup configuration: schedule_source='{startup_schedule_source}', plant='{startup_plant}'")
    
    shared_data = {
        # Dataframe that holds the manual schedule (random/CSV)
        "manual_schedule_df": pd.DataFrame(),
        # Dataframe that holds the API-fetched schedule
        "api_schedule_df": pd.DataFrame(),
        # Which schedule is currently active: 'manual' or 'api'
        "active_schedule_source": startup_schedule_source,
        # Dataframe that holds the measurements
        "measurements_df": pd.DataFrame(),
        # Current measurements filename (set by dashboard on start, read by measurement agent)
        "measurements_filename": None,
        # Lock for shared data
        "lock": threading.Lock(),
        # Event to signal shutdown
        "shutdown_event": threading.Event(),
        # API password (set by dashboard, read by data_fetcher)
        "api_password": None,
        # Data fetcher status (set by data_fetcher, read by dashboard)
        "data_fetcher_status": {
            "connected": False,
            "today_fetched": False,
            "tomorrow_fetched": False,
            "today_points": 0,
            "tomorrow_points": 0,
            "last_attempt": None,
            "error": None,
        },
        # Selected plant: 'local' or 'remote'
        "selected_plant": startup_plant,
        # Plant switching status: True when a switch is in progress
        "plant_switching": False,
        # Schedule switching status: True when a switch is in progress
        "schedule_switching": False,
    }
    
    try:
        # --- Create and start agent threads ---
        threads = [
            threading.Thread(target=data_fetcher_agent, args=(config, shared_data)),
            threading.Thread(target=scheduler_agent, args=(config, shared_data)),
            threading.Thread(target=plant_agent, args=(config, shared_data)),
            threading.Thread(target=measurement_agent, args=(config, shared_data)),
            threading.Thread(target=dashboard_agent, args=(config, shared_data))
        ]
        
        for t in threads:
            t.start()
        
        logging.info("All agents started.")
        logging.info("Dashboard available at http://127.0.0.1:8050/")
        logging.info("Use the dashboard to select a schedule mode and generate/upload a schedule.")
        
        # Run indefinitely until interrupted (no fixed end time)
        while not shared_data["shutdown_event"].is_set():
            time.sleep(1)
    
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
