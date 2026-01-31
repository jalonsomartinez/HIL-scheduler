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
    shared_data = {
        # Dataframe that holds the power schedule received from control center
        "schedule_final_df": pd.DataFrame(),
        # Dataframe that holds the measurements
        "measurements_df": pd.DataFrame(),
        # Lock for shared data
        "lock": threading.Lock(),
        # Event to signal shutdown
        "shutdown_event": threading.Event(),
        # Schedule manager reference (set by data_fetcher_agent)
        "schedule_manager": None,
        # Current schedule mode (set by dashboard)
        "schedule_mode": None,
        # Schedule mode parameters (set by dashboard)
        "schedule_mode_params": None,
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
