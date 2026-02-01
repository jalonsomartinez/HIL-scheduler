import logging
import time
import pandas as pd
from datetime import datetime
from pyModbusTCP.client import ModbusClient
from pyModbusTCP.utils import get_2comp, word_list_to_long
from utils import hw_to_kw


def measurement_agent(config, shared_data):
    """
    Reads data from the plant agent and logs it to a DataFrame and CSV file.
    The plant agent now provides all values including battery state and POI measurements.
    
    Filename Management:
    - Polls shared_data['measurements_filename'] every second for changes
    - When filename changes: writes to old file, clears DataFrame, starts new file
    - Measurements are taken according to MEASUREMENT_PERIOD_S config
    - Writes directly to shared_data for immediate visibility (no buffer)
    """
    logging.info("Measurement agent started.")
    
    # Single client to connect to the plant agent (which is the PPC interface)
    plant_client = ModbusClient(
        host=config["PLANT_MODBUS_HOST"],
        port=config["PLANT_MODBUS_PORT"]
    )
    
    last_measurement_time = time.time()
    last_filename_poll_time = time.time()
    last_write_time = time.time()
    
    # Track current filename and detect changes
    current_filename = None
    
    def write_measurements_to_csv(filename):
        """Write measurements DataFrame to CSV file."""
        if filename is None:
            return
            
        # Get reference to DataFrame with brief lock, then write outside lock
        with shared_data['lock']:
            measurements_df = shared_data['measurements_df'].copy()
        
        if not measurements_df.empty:
            logging.debug(
                f"Writing {len(measurements_df)} measurements to {filename}"
            )
            try:
                measurements_df.to_csv(filename, index=False)
            except Exception as e:
                logging.error(f"Error writing to CSV {filename}: {e}")
    
    def handle_filename_change(new_filename):
        """Handle filename change: write old data, clear DataFrame, start new."""
        nonlocal current_filename, last_write_time
        
        # Write existing DataFrame to old file
        if current_filename is not None:
            logging.info(f"Filename changed. Writing {len(shared_data['measurements_df'])} records to old file: {current_filename}")
            write_measurements_to_csv(current_filename)
        
        # Clear the measurements DataFrame for new file
        with shared_data['lock']:
            shared_data['measurements_df'] = pd.DataFrame()
        
        # Update current filename
        current_filename = new_filename
        last_write_time = time.time()
        
        logging.info(f"Started new measurements file: {current_filename}")
    
    def poll_filename():
        """Poll shared_data for filename changes."""
        nonlocal current_filename
        
        with shared_data['lock']:
            new_filename = shared_data.get('measurements_filename')
        
        # Check if filename has changed
        if new_filename != current_filename:
            # Check if new filename is valid (not None)
            if new_filename is not None:
                handle_filename_change(new_filename)
            else:
                # Filename was cleared (e.g., system stopped)
                if current_filename is not None:
                    write_measurements_to_csv(current_filename)
                    current_filename = None  # Stop writing to disk
                    logging.info("Measurements filename cleared. Stopped writing to disk.")
    
    def take_measurement():
        """Take a single measurement from the plant and write directly to shared_data."""
        if not plant_client.is_open:
            logging.info("Measurement agent trying to connect to Plant Modbus server...")
            if not plant_client.open():
                logging.warning(
                    "Measurement agent could not connect to Plant. Retrying..."
                )
                return False
            logging.info("Measurement agent connected to Plant Modbus server.")
        
        try:
            # Read all values from plant agent
            # 1. Active power setpoint (what scheduler sent)
            regs_p_setpoint = plant_client.read_holding_registers(
                config["PLANT_P_SETPOINT_REGISTER"], 2
            )
            if not regs_p_setpoint:
                logging.warning("Measurement agent could not read P setpoint from Plant.")
                return False
            
            p_setpoint_kw = hw_to_kw(
                get_2comp(word_list_to_long(regs_p_setpoint, big_endian=False)[0], 32)
            )
            
            # 2. Actual active power (after SoC limiting)
            regs_p_actual = plant_client.read_holding_registers(
                config["PLANT_P_BATTERY_ACTUAL_REGISTER"], 2
            )
            if not regs_p_actual:
                logging.warning(
                    "Measurement agent could not read actual P from Plant."
                )
                return False
            
            battery_active_power_kw = hw_to_kw(
                get_2comp(word_list_to_long(regs_p_actual, big_endian=False)[0], 32)
            )
            
            # 3. Reactive power setpoint (what scheduler sent)
            regs_q_setpoint = plant_client.read_holding_registers(
                config["PLANT_Q_SETPOINT_REGISTER"], 2
            )
            if not regs_q_setpoint:
                logging.warning("Measurement agent could not read Q setpoint from Plant.")
                return False
            
            q_setpoint_kvar = hw_to_kw(
                get_2comp(word_list_to_long(regs_q_setpoint, big_endian=False)[0], 32)
            )
            
            # 4. Actual reactive power (after limit clamping)
            regs_q_actual = plant_client.read_holding_registers(
                config["PLANT_Q_BATTERY_ACTUAL_REGISTER"], 2
            )
            if not regs_q_actual:
                logging.warning(
                    "Measurement agent could not read actual Q from Plant."
                )
                return False
            
            battery_reactive_power_kvar = hw_to_kw(
                get_2comp(word_list_to_long(regs_q_actual, big_endian=False)[0], 32)
            )
            
            # 5. State of Charge
            regs_soc = plant_client.read_holding_registers(
                config["PLANT_SOC_REGISTER"], 1
            )
            if not regs_soc:
                logging.warning("Measurement agent could not read SoC from Plant.")
                return False
            
            soc_pu = regs_soc[0] / 10000.0
            
            # 6. Active power at POI
            regs_p_poi = plant_client.read_holding_registers(
                config["PLANT_P_POI_REGISTER"], 2
            )
            if not regs_p_poi:
                logging.warning("Measurement agent could not read P_poi from Plant.")
                return False
            
            p_poi_kw = hw_to_kw(
                get_2comp(word_list_to_long(regs_p_poi, big_endian=False)[0], 32)
            )
            
            # 7. Reactive power at POI
            regs_q_poi = plant_client.read_holding_registers(
                config["PLANT_Q_POI_REGISTER"], 2
            )
            if not regs_q_poi:
                logging.warning("Measurement agent could not read Q_poi from Plant.")
                return False
            
            q_poi_kvar = hw_to_kw(
                get_2comp(word_list_to_long(regs_q_poi, big_endian=False)[0], 32)
            )
            
            # 8. Voltage at POI
            regs_v_poi = plant_client.read_holding_registers(
                config["PLANT_V_POI_REGISTER"], 1
            )
            if not regs_v_poi:
                logging.warning("Measurement agent could not read V_poi from Plant.")
                return False
            
            v_poi_pu = regs_v_poi[0] / 100.0
            
            logging.debug(
                f"Measurement: P_sp={p_setpoint_kw:.2f}kW, P_act={battery_active_power_kw:.2f}kW, "
                f"Q_sp={q_setpoint_kvar:.2f}kvar, Q_act={battery_reactive_power_kvar:.2f}kvar, "
                f"SoC={soc_pu:.4f}, P_poi={p_poi_kw:.2f}kW, Q_poi={q_poi_kvar:.2f}kvar, "
                f"V_poi={v_poi_pu:.4f}pu"
            )
            
            # Create measurement row and write directly to shared_data (no buffer)
            new_row = pd.DataFrame([{
                "timestamp": datetime.now(),
                "p_setpoint_kw": p_setpoint_kw,
                "battery_active_power_kw": battery_active_power_kw,
                "q_setpoint_kvar": q_setpoint_kvar,
                "battery_reactive_power_kvar": battery_reactive_power_kvar,
                "soc_pu": soc_pu,
                "p_poi_kw": p_poi_kw,
                "q_poi_kvar": q_poi_kvar,
                "v_poi_pu": v_poi_pu
            }])
            
            # Brief lock to append to shared DataFrame
            with shared_data['lock']:
                if shared_data['measurements_df'].empty:
                    shared_data['measurements_df'] = new_row
                else:
                    shared_data['measurements_df'] = pd.concat(
                        [shared_data['measurements_df'], new_row],
                        ignore_index=True
                    )
            
            return True
            
        except Exception as e:
            logging.error(f"Error taking measurement: {e}")
            return False
    
    # Main loop
    while not shared_data['shutdown_event'].is_set():
        current_time = time.time()
        
        # Poll filename every second
        if current_time - last_filename_poll_time >= 1.0:
            poll_filename()
            last_filename_poll_time = current_time
        
        # Take measurement according to configured period
        if current_time - last_measurement_time >= config["MEASUREMENT_PERIOD_S"]:
            if take_measurement():
                last_measurement_time = current_time
        
        # Periodically write to CSV (if we have a filename)
        if current_filename is not None and (current_time - last_write_time) >= config["MEASUREMENTS_WRITE_PERIOD_S"]:
            write_measurements_to_csv(current_filename)
            last_write_time = current_time
        
        # Small sleep to prevent busy-waiting
        time.sleep(0.1)
    
    # Cleanup - final write to CSV
    logging.info("Measurement agent stopping. Performing final write to CSV...")
    write_measurements_to_csv(current_filename)
    plant_client.close()
    logging.info("Measurement agent stopped.")
