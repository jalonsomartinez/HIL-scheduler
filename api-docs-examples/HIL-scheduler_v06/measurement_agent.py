import logging
import time
import pandas as pd
from datetime import datetime
from pyModbusTCP.client import ModbusClient
from pyModbusTCP.utils import get_2comp, word_list_to_long
from utils import hw_to_kw


def measurement_agent(config, shared_data):
    """
    Reads data from the battery agent and logs it to a DataFrame and CSV file.
    """
    logging.info("Measurement agent started.")

    ppc_client = ModbusClient(host=config["PPC_MODBUS_HOST"], port=config["PPC_MODBUS_PORT"])
    battery_client = ModbusClient(host=config["BATTERY_MODBUS_HOST"], port=config["BATTERY_MODBUS_PORT"])

    last_write_time = time.time()

    def write_measurements_to_csv():
        with shared_data['lock']:
            measurements_df = shared_data['measurements_df']
            if not measurements_df.empty:
                logging.debug(f"Writing {len(measurements_df)} measurements to {config['MEASUREMENTS_CSV']}")
                measurements_df.to_csv(config['MEASUREMENTS_CSV'], index=False)

    while not shared_data['shutdown_event'].is_set():
        start_loop_time = time.time()

        if not ppc_client.is_open:
            logging.info("Measurement agent trying to connect to PPC Modbus server...")
            if not ppc_client.open():
                logging.warning("Measurement agent could not connect to PPC. Retrying...")
                time.sleep(2)
                continue
            logging.info("Measurement agent connected to PPC Modbus server.")

        if not battery_client.is_open:
            logging.info("Measurement agent trying to connect to Battery Modbus server...")
            if not battery_client.open():
                logging.warning("Measurement agent could not connect to Battery. Retrying...")
                time.sleep(2)
                continue
            logging.info("Measurement agent connected to Battery Modbus server.")
        
        try:

            # 1. Read from PPC for original setpoint
            ppc_regs = ppc_client.read_holding_registers(config["PPC_SETPOINT_REGISTER"], 2)
            if not ppc_regs:
                logging.warning("Measurement agent could not read from PPC.")
                time.sleep(2)
                continue  # Skip this iteration if PPC reading fails

            original_setpoint_kw = hw_to_kw(get_2comp(word_list_to_long(ppc_regs, big_endian=False)[0],32))

            # 2. Read from Battery for actual setpoint and SoC
            battery_regs_setpoint = battery_client.read_holding_registers(config["BATTERY_SETPOINT_ACTUAL_REGISTER"], 2) # Read 2 registers
            if not battery_regs_setpoint:
                logging.warning("Measurement agent could not read from Battery.")
                time.sleep(2)
                continue  # Skip this iteration if Battery reading fails
            battery_regs_soc = battery_client.read_holding_registers(config["BATTERY_SOC_REGISTER"], 1) # Read 1 register
            if not battery_regs_soc:
                logging.warning("Measurement agent could not read from Battery.")
                time.sleep(2)
                continue  # Skip this iteration if Battery reading fails
            
            actual_setpoint_kw = hw_to_kw(get_2comp(word_list_to_long(battery_regs_setpoint, big_endian=False)[0],32))
            soc_pu = battery_regs_soc[0] / 10000.0

            logging.debug(f"Measurement: SP_orig={original_setpoint_kw:.2f}, SP_act={actual_setpoint_kw:.2f}, SoC={soc_pu:.2f}")

            # 3. Append to shared dataframe
            new_row = pd.DataFrame([{
                "timestamp": datetime.now(),
                "original_setpoint_kw": original_setpoint_kw,
                "actual_setpoint_kw": actual_setpoint_kw,
                "soc_pu": soc_pu
            }])
            
            with shared_data['lock']:
                if shared_data['measurements_df'].empty:
                    shared_data['measurements_df'] = new_row
                else:
                    shared_data['measurements_df'] = pd.concat([shared_data['measurements_df'], new_row], ignore_index=True)
            
            # Periodically write to CSV
            if time.time() - last_write_time >= config["MEASUREMENTS_WRITE_PERIOD_S"]:
                write_measurements_to_csv()
                last_write_time = time.time()

        except Exception as e:
            logging.error(f"Error in measurement agent: {e}")
        
        time.sleep(max(0, config["MEASUREMENT_PERIOD_S"] - (time.time() - start_loop_time)))
    
    logging.info("Measurement agent stopping. Performing final write to CSV..")

    write_measurements_to_csv()
    ppc_client.close()
    battery_client.close()
    logging.info("Measurement agent stopped.")
