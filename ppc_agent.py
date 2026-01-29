import logging
import time
from pyModbusTCP.client import ModbusClient
from pyModbusTCP.server import ModbusServer
from pyModbusTCP.utils import long_list_to_word


def ppc_agent(config, shared_data):
    """
    Forwards setpoints from its Modbus server to the battery, based on an enable flag.
    """
    logging.info("PPC agent started.")

    # --- Setup Modbus ---
    ppc_server = ModbusServer(host=config["PPC_MODBUS_HOST"], port=config["PPC_MODBUS_PORT"], no_block=True)
    # Start local plant emulation modbus servers
    logging.info("Starting PPC Modbus server...")
    ppc_server.start()
    logging.info("PPC Modbus server started.")
    battery_client = ModbusClient(host=config["BATTERY_MODBUS_HOST"], port=config["BATTERY_MODBUS_PORT"])

    while not shared_data['shutdown_event'].is_set():
        start_loop_time = time.time()

        if not battery_client.is_open:
            logging.info("PPC trying to connect to Battery Modbus server...")
            if not battery_client.open():
                logging.warning("PPC could not connect to Battery. Retrying...")
                time.sleep(2)
                continue
            logging.info("PPC connected to Battery Modbus server.")
        
        try:
            # Read the enable flag and setpoint from its own Modbus server databank.
            # We read both registers in one call for efficiency.
            regs_setpoint = ppc_server.data_bank.get_holding_registers(config["PPC_SETPOINT_REGISTER"], 2)
            regs_enable = ppc_server.data_bank.get_holding_registers(config["PPC_ENABLE_REGISTER"], 1)
            if regs_setpoint and regs_enable:
                if regs_enable[0] == 1:
                    # If enabled, forward the setpoint from the scheduler
                    logging.debug(f"PPC enabled. Forwarding setpoint register value {regs_setpoint} to battery.")
                    battery_client.write_multiple_registers(config["BATTERY_SETPOINT_IN_REGISTER"], regs_setpoint)
                else:
                    # If disabled, send a 0kW setpoint
                    logging.debug("PPC disabled. Sending 0kW setpoint to battery.")
                    battery_client.write_multiple_registers(config["BATTERY_SETPOINT_IN_REGISTER"], long_list_to_word([0], big_endian=False))
            else:
                logging.warning("PPC could not read registers from its own server.")

        except Exception as e:
            logging.error(f"Error in PPC agent: {e}")
        
        time.sleep(max(0, config["PPC_PERIOD_S"] - (time.time() - start_loop_time)))
    
    battery_client.close()
    logging.info("Stopping PPC Modbus server...")
    ppc_server.stop()
    logging.info("PPC Modbus server stopped.")
    logging.info("PPC agent stopped.")
