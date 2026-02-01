import logging
import time
import pandas as pd
from datetime import datetime
from pyModbusTCP.client import ModbusClient
from pyModbusTCP.utils import long_list_to_word
from utils import kw_to_hw


def scheduler_agent(config, shared_data):
    """
    Checks the active schedule every second and sends setpoint updates to the plant agent
    via Modbus. Sends both active power (P) and reactive power (Q) setpoints.
    
    Reads the active_schedule_source to determine which schedule to use:
    - 'manual': Use manual_schedule_df
    - 'api': Use api_schedule_df
    
    Dynamically switches between local and remote plant based on selected_plant in shared_data.
    """
    logging.info("Scheduler agent started.")
    
    # Track current plant selection and config
    current_plant = None
    client = None
    
    def get_plant_config(plant_type):
        """Get Modbus configuration for the selected plant."""
        if plant_type == 'remote':
            return {
                'host': config.get("PLANT_REMOTE_MODBUS_HOST", "10.117.133.21"),
                'port': config.get("PLANT_REMOTE_MODBUS_PORT", 502),
                'p_setpoint_reg': config.get("PLANT_REMOTE_P_SETPOINT_REGISTER", 0),
                'q_setpoint_reg': config.get("PLANT_REMOTE_Q_SETPOINT_REGISTER", 4),
            }
        else:  # local
            return {
                'host': config.get("PLANT_LOCAL_MODBUS_HOST", "localhost"),
                'port': config.get("PLANT_LOCAL_MODBUS_PORT", 5020),
                'p_setpoint_reg': config.get("PLANT_P_SETPOINT_REGISTER", 0),
                'q_setpoint_reg': config.get("PLANT_Q_SETPOINT_REGISTER", 4),
            }
    
    def connect_to_plant(plant_type):
        """Create and return a new Modbus client for the specified plant."""
        nonlocal client
        if client is not None:
            try:
                client.close()
            except:
                pass
        
        plant_config = get_plant_config(plant_type)
        client = ModbusClient(
            host=plant_config['host'],
            port=plant_config['port']
        )
        logging.info(f"Scheduler: Switched to {plant_type} plant at {plant_config['host']}:{plant_config['port']}")
        return plant_config
    
    current_p_setpoint = None
    current_q_setpoint = None
    previous_p_setpoint = None
    previous_q_setpoint = None
    last_active_source = None
    
    while not shared_data['shutdown_event'].is_set():
        start_loop_time = time.time()
        
        # Check for plant selection change
        with shared_data['lock']:
            selected_plant = shared_data.get('selected_plant', 'local')
        
        if selected_plant != current_plant:
            plant_config = connect_to_plant(selected_plant)
            current_plant = selected_plant
        else:
            plant_config = get_plant_config(current_plant)
        
        if client is None:
            plant_config = get_plant_config(selected_plant)
            client = ModbusClient(
                host=plant_config['host'],
                port=plant_config['port']
            )
        
        if not client.is_open:
            logging.info(f"Scheduler trying to connect to {current_plant} Plant Modbus server at {plant_config['host']}:{plant_config['port']}...")
            if not client.open():
                logging.warning("Scheduler could not connect to Plant. Retrying...")
                time.sleep(2)
                continue
            logging.info("Scheduler connected to Plant Modbus server.")
        
        try:
            # Get schedule reference with minimal lock time
            # In Python, reading a dict key is atomic due to GIL, but we use lock for consistency
            with shared_data['lock']:
                active_source = shared_data.get('active_schedule_source', 'manual')
                
                # Get the appropriate schedule reference (just the reference, not a copy)
                if active_source == 'api':
                    schedule_df = shared_data.get('api_schedule_df')
                else:  # default to manual
                    schedule_df = shared_data.get('manual_schedule_df')
            
            # Log when source changes (outside lock)
            if active_source != last_active_source:
                logging.info(f"Scheduler: Active schedule source changed to '{active_source}'")
                last_active_source = active_source
            
            # Handle None or empty schedule - send 0 setpoint
            if schedule_df is None or schedule_df.empty:
                # logging.info("No schedule available, sending 0 setpoint")
                p_reg_val = long_list_to_word([0], big_endian=False)
                q_reg_val = long_list_to_word([0], big_endian=False)
                client.write_multiple_registers(plant_config['p_setpoint_reg'], p_reg_val)
                client.write_multiple_registers(plant_config['q_setpoint_reg'], q_reg_val)
                time.sleep(config["SCHEDULER_PERIOD_S"])
                continue
            
            # Use asof for robust lookup (outside lock - DataFrame is not being modified)
            current_row = schedule_df.asof(datetime.now())
            
            if current_row is None or current_row.empty:
                logging.info("No current row found in schedule, sending 0 setpoint")
                current_p_setpoint = 0.0
                current_q_setpoint = 0.0
            else:
                current_p_setpoint = current_row['power_setpoint_kw']
                current_q_setpoint = current_row.get('reactive_power_setpoint_kvar', 0.0)
                
                # Check for NaN values and send 0 instead
                if pd.isna(current_p_setpoint) or pd.isna(current_q_setpoint):
                    logging.warning(f"NaN setpoint found in schedule, sending 0 instead")
                    current_p_setpoint = 0.0
                    current_q_setpoint = 0.0
            
            # Send active power setpoint if changed (outside lock)
            if current_p_setpoint != previous_p_setpoint:
                logging.info(
                    f"New active power setpoint: {current_p_setpoint:.2f} kW. Sending to Plant."
                )
                
                # Convert to hW and then to 32-bit signed integer for Modbus
                p_reg_val = long_list_to_word(
                    [kw_to_hw(current_p_setpoint)],
                    big_endian=False
                )
                
                client.write_multiple_registers(
                    plant_config['p_setpoint_reg'],
                    p_reg_val
                )
                previous_p_setpoint = current_p_setpoint
            
            # Send reactive power setpoint if changed (outside lock)
            if current_q_setpoint != previous_q_setpoint:
                logging.info(
                    f"New reactive power setpoint: {current_q_setpoint:.2f} kvar. Sending to Plant."
                )
                
                # Convert to hW and then to 32-bit signed integer for Modbus
                q_reg_val = long_list_to_word(
                    [kw_to_hw(current_q_setpoint)],
                    big_endian=False
                )
                
                client.write_multiple_registers(
                    plant_config['q_setpoint_reg'],
                    q_reg_val
                )
                previous_q_setpoint = current_q_setpoint
        
        except Exception as e:
            logging.error(f"Error in scheduler agent: {e}")
        
        # Ensure loop runs at the desired frequency
        time.sleep(max(0, config["SCHEDULER_PERIOD_S"] - (time.time() - start_loop_time)))
    
    client.close()
    logging.info("Scheduler agent stopped.")
