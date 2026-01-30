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
    """
    logging.info("Measurement agent started.")
    
    # Single client to connect to the plant agent (which is the PPC interface)
    plant_client = ModbusClient(
        host=config["PLANT_MODBUS_HOST"],
        port=config["PLANT_MODBUS_PORT"]
    )
    
    last_write_time = time.time()
    
    def write_measurements_to_csv():
        with shared_data['lock']:
            measurements_df = shared_data['measurements_df']
            if not measurements_df.empty:
                logging.debug(
                    f"Writing {len(measurements_df)} measurements to "
                    f"{config['MEASUREMENTS_CSV']}"
                )
                measurements_df.to_csv(config['MEASUREMENTS_CSV'], index=False)
    
    while not shared_data['shutdown_event'].is_set():
        start_loop_time = time.time()
        
        if not plant_client.is_open:
            logging.info("Measurement agent trying to connect to Plant Modbus server...")
            if not plant_client.open():
                logging.warning(
                    "Measurement agent could not connect to Plant. Retrying..."
                )
                time.sleep(2)
                continue
            logging.info("Measurement agent connected to Plant Modbus server.")
        
        try:
            # Read all values from plant agent
            # 1. Active power setpoint (what scheduler sent)
            regs_p_setpoint = plant_client.read_holding_registers(
                config["PLANT_P_SETPOINT_REGISTER"], 2
            )
            if not regs_p_setpoint:
                logging.warning("Measurement agent could not read P setpoint from Plant.")
                time.sleep(2)
                continue
            
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
                time.sleep(2)
                continue
            
            battery_active_power_kw = hw_to_kw(
                get_2comp(word_list_to_long(regs_p_actual, big_endian=False)[0], 32)
            )
            
            # 3. Reactive power setpoint (what scheduler sent)
            regs_q_setpoint = plant_client.read_holding_registers(
                config["PLANT_Q_SETPOINT_REGISTER"], 2
            )
            if not regs_q_setpoint:
                logging.warning("Measurement agent could not read Q setpoint from Plant.")
                time.sleep(2)
                continue
            
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
                time.sleep(2)
                continue
            
            battery_reactive_power_kvar = hw_to_kw(
                get_2comp(word_list_to_long(regs_q_actual, big_endian=False)[0], 32)
            )
            
            # 5. State of Charge
            regs_soc = plant_client.read_holding_registers(
                config["PLANT_SOC_REGISTER"], 1
            )
            if not regs_soc:
                logging.warning("Measurement agent could not read SoC from Plant.")
                time.sleep(2)
                continue
            
            soc_pu = regs_soc[0] / 10000.0
            
            # 6. Active power at POI
            regs_p_poi = plant_client.read_holding_registers(
                config["PLANT_P_POI_REGISTER"], 2
            )
            if not regs_p_poi:
                logging.warning("Measurement agent could not read P_poi from Plant.")
                time.sleep(2)
                continue
            
            p_poi_kw = hw_to_kw(
                get_2comp(word_list_to_long(regs_p_poi, big_endian=False)[0], 32)
            )
            
            # 7. Reactive power at POI
            regs_q_poi = plant_client.read_holding_registers(
                config["PLANT_Q_POI_REGISTER"], 2
            )
            if not regs_q_poi:
                logging.warning("Measurement agent could not read Q_poi from Plant.")
                time.sleep(2)
                continue
            
            q_poi_kvar = hw_to_kw(
                get_2comp(word_list_to_long(regs_q_poi, big_endian=False)[0], 32)
            )
            
            # 8. Voltage at POI
            regs_v_poi = plant_client.read_holding_registers(
                config["PLANT_V_POI_REGISTER"], 1
            )
            if not regs_v_poi:
                logging.warning("Measurement agent could not read V_poi from Plant.")
                time.sleep(2)
                continue
            
            v_poi_pu = regs_v_poi[0] / 100.0
            
            logging.debug(
                f"Measurement: P_sp={p_setpoint_kw:.2f}kW, P_act={battery_active_power_kw:.2f}kW, "
                f"Q_sp={q_setpoint_kvar:.2f}kvar, Q_act={battery_reactive_power_kvar:.2f}kvar, "
                f"SoC={soc_pu:.4f}, P_poi={p_poi_kw:.2f}kW, Q_poi={q_poi_kvar:.2f}kvar, "
                f"V_poi={v_poi_pu:.4f}pu"
            )
            
            # Append to shared dataframe
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
            
            with shared_data['lock']:
                if shared_data['measurements_df'].empty:
                    shared_data['measurements_df'] = new_row
                else:
                    shared_data['measurements_df'] = pd.concat(
                        [shared_data['measurements_df'], new_row],
                        ignore_index=True
                    )
            
            # Periodically write to CSV
            if time.time() - last_write_time >= config["MEASUREMENTS_WRITE_PERIOD_S"]:
                write_measurements_to_csv()
                last_write_time = time.time()
            
        except Exception as e:
            logging.error(f"Error in measurement agent: {e}")
        
        time.sleep(max(0, config["MEASUREMENT_PERIOD_S"] - (time.time() - start_loop_time)))
    
    # Cleanup
    logging.info("Measurement agent stopping. Performing final write to CSV...")
    write_measurements_to_csv()
    plant_client.close()
    logging.info("Measurement agent stopped.")
