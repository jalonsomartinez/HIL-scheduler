import logging
import time
import numpy as np
import cmath
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
    
    # Plant model parameters
    r_ohm = config["PLANT_R_OHM"]
    x_ohm = config["PLANT_X_OHM"]
    v_nom_v = config["PLANT_NOMINAL_VOLTAGE_V"]
    base_power_kva = config["PLANT_BASE_POWER_KVA"]
    power_factor = config["PLANT_POWER_FACTOR"]
    
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
    
    def calculate_poi_power(p_batt_kw, v_nom_v, r_ohm, x_ohm, power_factor):
        """
        Calculate power at POI using impedance model.
        
        Args:
            p_batt_kw: Battery active power (kW)
            v_nom_v: Nominal line-to-line voltage (V)
            r_ohm: Resistance (ohms)
            x_ohm: Reactance (ohms)
            power_factor: Battery power factor
            
        Returns:
            tuple: (p_poi_kw, q_poi_kvar, v_poi_pu)
        """
        if abs(p_batt_kw) < 0.01:
            # No power flow - no losses, no reactive power
            return 0.0, 0.0, 1.0
        
        # Calculate apparent and reactive power at battery
        s_batt_kva = abs(p_batt_kw) / power_factor
        # Reactive power has same sign as active power for inductive load
        q_batt_kvar = np.sign(p_batt_kw) * np.sqrt(max(0, s_batt_kva**2 - p_batt_kw**2))
        
        # Convert to phase values
        v_nom_ll_kv = v_nom_v / 1000.0  # Line-to-line in kV
        v_nom_ph_kv = v_nom_ll_kv / np.sqrt(3)  # Phase voltage in kV
        
        # Calculate current (per phase)
        s_batt_per_phase_kva = s_batt_kva / 3.0
        i_ka = s_batt_per_phase_kva / v_nom_ph_kv  # Current in kA
        
        # Current phase angle
        phi = np.arccos(power_factor)
        if p_batt_kw < 0:
            phi = -phi  # Current direction reverses for charging
        
        # Complex current (conjugate for power calculation convention)
        i_complex = i_ka * cmath.exp(-1j * phi)
        
        # Voltage drop across impedance
        z_ohm = complex(r_ohm, x_ohm)
        v_drop_kv = i_complex * z_ohm / 1000.0  # Convert ohms to kV drop
        
        # POI voltage
        v_poi_kv = v_nom_ph_kv - v_drop_kv
        v_poi_pu = abs(v_poi_kv) / v_nom_ph_kv
        
        # Power at POI (accounting for losses)
        # S_poi = V_poi * I* (conjugate)
        s_poi_per_phase_kva = v_poi_kv * i_complex.conjugate()
        s_poi_kva = 3.0 * s_poi_per_phase_kva
        
        p_poi_kw = s_poi_kva.real
        q_poi_kvar = s_poi_kva.imag
        
        return p_poi_kw, q_poi_kvar, v_poi_pu
    
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
            
            # --- Plant Model Calculation ---
            p_poi_kw, q_poi_kvar, v_poi_pu = calculate_poi_power(
                actual_p_kw, v_nom_v, r_ohm, x_ohm, power_factor
            )
            
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
