import logging
import time
import numpy as np
from pyModbusTCP.server import ModbusServer
from pyModbusTCP.utils import get_2comp, word_list_to_long, long_list_to_word
from utils import kw_to_hw, hw_to_kw


def plant_agent(config, shared_data):
    """
    Plant Agent - Merged functionality of PPC and Battery agents.
    
    Provides a single Modbus server interface for the scheduler and dashboard,
    internally simulates the battery with SoC tracking and power limiting,
    and computes plant model values (P/Q at POI) using impedance model.
    """
    logging.info("Plant agent started.")
    
    # --- Setup Modbus Server ---
    plant_server = ModbusServer(
        host=config["PLANT_MODBUS_HOST"],
        port=config["PLANT_MODBUS_PORT"],
        no_block=True
    )
    logging.info("Starting Plant Modbus server...")
    plant_server.start()
    logging.info(f"Plant Modbus server started on {config['PLANT_MODBUS_HOST']}:{config['PLANT_MODBUS_PORT']}")
    
    # --- Initialize Battery State ---
    capacity_kwh = config["PLANT_CAPACITY_KWH"]
    soc_pu = config["PLANT_INITIAL_SOC_PU"]
    soc_kwh = soc_pu * capacity_kwh
    dt_s = config["PLANT_PERIOD_S"]
    dt_h = dt_s / 3600.0
    
    # POI voltage (fixed value, no impedance model)
    poi_voltage_v = config["PLANT_POI_VOLTAGE_V"]
    
    # Power limits
    p_max_kw = config["PLANT_P_MAX_KW"]
    p_min_kw = config["PLANT_P_MIN_KW"]
    q_max_kvar = config["PLANT_Q_MAX_KVAR"]
    q_min_kvar = config["PLANT_Q_MIN_KVAR"]
    
    # Limitation tracking
    was_limited_previously = False
    previous_limited_power_kw = None
    
    # Initialize Modbus registers with default values
    plant_server.data_bank.set_holding_registers(config["PLANT_ENABLE_REGISTER"], [0])  # Disabled by default
    plant_server.data_bank.set_holding_registers(config["PLANT_SOC_REGISTER"], [int(soc_pu * 10000)])
    
    # Active power setpoint registers
    plant_server.data_bank.set_holding_registers(
        config["PLANT_P_SETPOINT_REGISTER"],
        long_list_to_word([0], big_endian=False)
    )
    plant_server.data_bank.set_holding_registers(
        config["PLANT_P_BATTERY_ACTUAL_REGISTER"],
        long_list_to_word([0], big_endian=False)
    )
    
    # Reactive power setpoint registers
    plant_server.data_bank.set_holding_registers(
        config["PLANT_Q_SETPOINT_REGISTER"],
        long_list_to_word([0], big_endian=False)
    )
    plant_server.data_bank.set_holding_registers(
        config["PLANT_Q_BATTERY_ACTUAL_REGISTER"],
        long_list_to_word([0], big_endian=False)
    )
    
    # POI measurement registers
    plant_server.data_bank.set_holding_registers(
        config["PLANT_P_POI_REGISTER"],
        long_list_to_word([0], big_endian=False)
    )
    plant_server.data_bank.set_holding_registers(
        config["PLANT_Q_POI_REGISTER"],
        long_list_to_word([0], big_endian=False)
    )
    plant_server.data_bank.set_holding_registers(config["PLANT_V_POI_REGISTER"], [10000])  # 1.0 pu
    
    while not shared_data['shutdown_event'].is_set():
        start_loop_time = time.time()
        
        try:
            # --- Read inputs from Modbus ---
            regs_p_setpoint = plant_server.data_bank.get_holding_registers(
                config["PLANT_P_SETPOINT_REGISTER"], 2
            )
            regs_q_setpoint = plant_server.data_bank.get_holding_registers(
                config["PLANT_Q_SETPOINT_REGISTER"], 2
            )
            regs_enable = plant_server.data_bank.get_holding_registers(
                config["PLANT_ENABLE_REGISTER"], 1
            )
            
            if not regs_p_setpoint or not regs_q_setpoint or not regs_enable:
                logging.warning("Plant agent could not read registers from Modbus server.")
                time.sleep(max(0, dt_s - (time.time() - start_loop_time)))
                continue
            
            # Decode active power setpoint
            original_p_kw = hw_to_kw(
                get_2comp(word_list_to_long(regs_p_setpoint, big_endian=False)[0], 32)
            )
            
            # Decode reactive power setpoint
            original_q_kvar = hw_to_kw(
                get_2comp(word_list_to_long(regs_q_setpoint, big_endian=False)[0], 32)
            )
            
            # Check enable flag
            is_enabled = regs_enable[0] == 1
            
            if not is_enabled:
                # When disabled, force setpoints to 0
                original_p_kw = 0.0
                original_q_kvar = 0.0
            
            # --- Active Power: Battery Simulation with SoC Limiting ---
            actual_p_kw = original_p_kw
            future_soc_kwh = soc_kwh - (original_p_kw * dt_h)
            is_limited_now = False
            limit_reason = ""
            
            # Check for boundary violations and calculate limited power
            if future_soc_kwh > capacity_kwh:
                is_limited_now = True
                limit_reason = "SoC would exceed capacity"
                # Would overcharge - limit charging power
                p_lim_kw = (soc_kwh - capacity_kwh) / dt_h
                actual_p_kw = max(original_p_kw, p_lim_kw)
            elif future_soc_kwh < 0:
                is_limited_now = True
                limit_reason = "SoC would fall below zero"
                # Would over-discharge - limit discharging power
                p_lim_kw = soc_kwh / dt_h
                actual_p_kw = min(original_p_kw, p_lim_kw)
            
            # Handle logging based on limitation state changes
            if is_limited_now:
                if not was_limited_previously or not np.isclose(
                    actual_p_kw, previous_limited_power_kw
                ):
                    logging.warning(
                        f"{limit_reason}. Limiting active power from "
                        f"{original_p_kw:.2f}kW to {actual_p_kw:.2f}kW"
                    )
                previous_limited_power_kw = actual_p_kw
            elif was_limited_previously:
                logging.info("Active power limitation removed. Battery operating normally.")
                previous_limited_power_kw = None
            
            was_limited_previously = is_limited_now
            
            # Apply the actual (limited) active power setpoint and update SoC
            soc_kwh -= actual_p_kw * dt_h
            soc_kwh = max(0, min(capacity_kwh, soc_kwh))  # Clamp to be safe
            soc_pu = soc_kwh / capacity_kwh
            
            # --- Reactive Power: Apply limits (NOT SoC limited) ---
            actual_q_kvar = original_q_kvar
            # Clamp reactive power to limits
            if actual_q_kvar > q_max_kvar:
                actual_q_kvar = q_max_kvar
                logging.warning(f"Reactive power limited to {q_max_kvar:.2f}kvar (max)")
            elif actual_q_kvar < q_min_kvar:
                actual_q_kvar = q_min_kvar
                logging.warning(f"Reactive power limited to {q_min_kvar:.2f}kvar (min)")
            
            # --- Plant Model Calculation (simplified - no impedance) ---
            # Plant power equals battery power (no losses)
            p_poi_kw = actual_p_kw
            q_poi_kvar = actual_q_kvar
            # POI voltage is fixed from config (20 kV in Volts)
            # Convert to per-unit for Modbus register (assuming 20 kV nominal)
            v_poi_pu = poi_voltage_v / 20000.0
            
            # --- Update Modbus Registers ---
            # Active power battery actual
            p_actual_reg = long_list_to_word(
                [kw_to_hw(actual_p_kw)], big_endian=False
            )
            plant_server.data_bank.set_holding_registers(
                config["PLANT_P_BATTERY_ACTUAL_REGISTER"], p_actual_reg
            )
            
            # Reactive power battery actual
            q_actual_reg = long_list_to_word(
                [kw_to_hw(actual_q_kvar)], big_endian=False
            )
            plant_server.data_bank.set_holding_registers(
                config["PLANT_Q_BATTERY_ACTUAL_REGISTER"], q_actual_reg
            )
            
            # SoC
            soc_reg = int(soc_pu * 10000)
            plant_server.data_bank.set_holding_registers(
                config["PLANT_SOC_REGISTER"], [soc_reg]
            )
            
            # P at POI
            p_poi_reg = long_list_to_word([kw_to_hw(p_poi_kw)], big_endian=False)
            plant_server.data_bank.set_holding_registers(
                config["PLANT_P_POI_REGISTER"], p_poi_reg
            )
            
            # Q at POI
            q_poi_reg = long_list_to_word([kw_to_hw(q_poi_kvar)], big_endian=False)
            plant_server.data_bank.set_holding_registers(
                config["PLANT_Q_POI_REGISTER"], q_poi_reg
            )
            
            # V at POI
            v_poi_reg = int(v_poi_pu * 100)
            plant_server.data_bank.set_holding_registers(
                config["PLANT_V_POI_REGISTER"], [v_poi_reg]
            )
            
            logging.debug(
                f"Plant: P_sp={original_p_kw:.2f}kW, P_act={actual_p_kw:.2f}kW, "
                f"Q_sp={original_q_kvar:.2f}kvar, Q_act={actual_q_kvar:.2f}kvar, "
                f"SoC={soc_pu:.4f}, P_poi={p_poi_kw:.2f}kW, Q_poi={q_poi_kvar:.2f}kvar, "
                f"V_poi={v_poi_pu:.4f}pu"
            )
            
        except Exception as e:
            logging.error(f"Error in plant agent: {e}")
        
        time.sleep(max(0, dt_s - (time.time() - start_loop_time)))
    
    # --- Cleanup ---
    logging.info("Stopping Plant Modbus server...")
    plant_server.stop()
    logging.info("Plant Modbus server stopped.")
    logging.info("Plant agent stopped.")
