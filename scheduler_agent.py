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
    """
    logging.info("Scheduler agent started.")
    
    client = ModbusClient(
        host=config["PLANT_MODBUS_HOST"],
        port=config["PLANT_MODBUS_PORT"]
    )
    current_p_setpoint = None
    current_q_setpoint = None
    previous_p_setpoint = None
    previous_q_setpoint = None
    last_active_source = None
    
    while not shared_data['shutdown_event'].is_set():
        start_loop_time = time.time()
        
        if not client.is_open:
            logging.info("Scheduler trying to connect to Plant Modbus server...")
            if not client.open():
                logging.warning("Scheduler could not connect to Plant. Retrying...")
                time.sleep(2)
                continue
            logging.info("Scheduler connected to Plant Modbus server.")
        
        try:
            with shared_data['lock']:
                # Determine which schedule is active
                active_source = shared_data.get('active_schedule_source', 'manual')
                
                # Log when source changes
                if active_source != last_active_source:
                    logging.info(f"Scheduler: Active schedule source changed to '{active_source}'")
                    last_active_source = active_source
                
                # Get the appropriate schedule
                if active_source == 'api':
                    schedule_df = shared_data.get('api_schedule_df')
                else:  # default to manual
                    schedule_df = shared_data.get('manual_schedule_df')
                
                # Handle None case - send 0 setpoint when no schedule available
                if schedule_df is None or schedule_df.empty:
                    # Send 0 setpoint for both P and Q
                    logging.info("No schedule available, sending 0 setpoint")
                    p_reg_val = long_list_to_word([0], big_endian=False)
                    q_reg_val = long_list_to_word([0], big_endian=False)
                    client.write_multiple_registers(config["PLANT_P_SETPOINT_REGISTER"], p_reg_val)
                    client.write_multiple_registers(config["PLANT_Q_SETPOINT_REGISTER"], q_reg_val)
                    time.sleep(config["SCHEDULER_PERIOD_S"])
                    continue
                
                # Use asof for robust lookup
                current_row = schedule_df.asof(datetime.now())
                current_p_setpoint = current_row['power_setpoint_kw']
                current_q_setpoint = current_row.get('reactive_power_setpoint_kvar', 0.0)

                # Check for NaN values and send 0 instead
                if pd.isna(current_p_setpoint) or pd.isna(current_q_setpoint):
                    logging.warning(f"NaN setpoint found in schedule, sending 0 instead")
                    current_p_setpoint = 0.0
                    current_q_setpoint = 0.0
            
            # Send active power setpoint if changed
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
                    config["PLANT_P_SETPOINT_REGISTER"],
                    p_reg_val
                )
                previous_p_setpoint = current_p_setpoint
            
            # Send reactive power setpoint if changed
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
                    config["PLANT_Q_SETPOINT_REGISTER"],
                    q_reg_val
                )
                previous_q_setpoint = current_q_setpoint
        
        except Exception as e:
            logging.error(f"Error in scheduler agent: {e}")
        
        # Ensure loop runs at the desired frequency
        time.sleep(max(0, config["SCHEDULER_PERIOD_S"] - (time.time() - start_loop_time)))
    
    client.close()
    logging.info("Scheduler agent stopped.")
