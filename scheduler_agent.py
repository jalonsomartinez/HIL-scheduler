import logging
import time
from datetime import datetime
from pyModbusTCP.client import ModbusClient
from pyModbusTCP.utils import long_list_to_word
from utils import kw_to_hw

def scheduler_agent(config, shared_data):
    """
    Checks the schedule every second and sends setpoint updates to the PPC agent
    via Modbus.
    """
    logging.info("Scheduler agent started.")
    
    client = ModbusClient(host=config["PPC_MODBUS_HOST"], port=config["PPC_MODBUS_PORT"])
    current_setpoint = None
    previous_setpoint = None

    while not shared_data['shutdown_event'].is_set():
        start_loop_time = time.time()

        if not client.is_open:
            logging.info("Scheduler trying to connect to PPC Modbus server...")
            if not client.open():
                logging.warning("Scheduler could not connect to PPC. Retrying...")
                time.sleep(2)
                continue
            logging.info("Scheduler connected to PPC Modbus server.")

        try:
            with shared_data['lock']:
                schedule_final_df = shared_data['schedule_final_df']
                if schedule_final_df.empty:
                    time.sleep(config["SCHEDULER_PERIOD_S"])
                    continue
                # Use asof for robust lookup
                current_setpoint = schedule_final_df.asof(datetime.now())['power_setpoint_kw']

            if current_setpoint != previous_setpoint:
                logging.info(f"New setpoint: {current_setpoint:.2f} kW. Sending to PPC.")
                
                # Convert to hW and then to 32-bit signed integer for Modbus
                reg_val = long_list_to_word([kw_to_hw(current_setpoint)],big_endian=False)

                client.write_multiple_registers(config["PPC_SETPOINT_REGISTER"], reg_val)
                previous_setpoint = current_setpoint

        except Exception as e:
            logging.error(f"Error in scheduler agent: {e}")
        
        # Ensure loop runs at the desired frequency
        time.sleep(max(0, config["SCHEDULER_PERIOD_S"] - (time.time() - start_loop_time)))
    
    client.close()
    logging.info("Scheduler agent stopped.")