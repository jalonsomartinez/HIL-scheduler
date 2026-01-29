import logging
import time
import numpy as np
from pyModbusTCP.server import ModbusServer
from pyModbusTCP.utils import get_2comp, word_list_to_long, long_list_to_word
from utils import kw_to_hw, hw_to_kw

def battery_agent(config, shared_data):
    """
    Simulates the battery, applying power setpoints and managing its state of charge (SoC).
    """
    logging.info("Battery agent started.")

    # --- Setup Modbus Server ---
    battery_server = ModbusServer(host=config["BATTERY_MODBUS_HOST"], port=config["BATTERY_MODBUS_PORT"], no_block=True)
    logging.info("Starting Battery Modbus server...")
    battery_server.start()
    logging.info("Battery Modbus server started.")

    capacity_kwh = config["BATTERY_CAPACITY_KWH"]
    soc_pu      = config["BATTERY_INITIAL_SOC_PU"]
    soc_kwh     = soc_pu * capacity_kwh
    dt_s = config["BATTERY_PERIOD_S"]
    dt_h = dt_s / 3600.0
    was_limited_previously = False # Track limitation state
    previous_limited_power_kw = None # Track the last limited power value

    while not shared_data['shutdown_event'].is_set():
        start_loop_time = time.time()

        try:
            # Read incoming setpoint from its own Modbus databank
            regs = battery_server.data_bank.get_holding_registers(config["BATTERY_SETPOINT_IN_REGISTER"], 2)
            if not regs:
                time.sleep(max(0, dt_s - (time.time() - start_loop_time)))
                continue
            
            # 1. Decode original setpoint
            original_power_kw = hw_to_kw(get_2comp(word_list_to_long(regs, big_endian=False)[0],32))
            
            # 2. Calculate future SoC and limit power if necessary
            actual_power_kw = original_power_kw
            future_soc_kwh = soc_kwh - (original_power_kw * dt_h) # Negative power = charging = SoC increases
            is_limited_now = False
            limit_reason = ""

            # Check for boundary violations and calculate limited power
            if future_soc_kwh > capacity_kwh:
                is_limited_now = True
                limit_reason = "SoC would exceed capacity"
                # Would overcharge. Limit charging power.
                # We need soc_kwh - p_lim * dt_h = capacity_kwh
                p_lim_kw = (soc_kwh - capacity_kwh) / dt_h
                actual_power_kw = max(original_power_kw, p_lim_kw)
            elif future_soc_kwh < 0:
                is_limited_now = True
                limit_reason = "SoC would fall below zero"
                # Would over-discharge. Limit discharging power.
                # We need soc_kwh - p_lim * dt_h = 0
                p_lim_kw = soc_kwh / dt_h
                actual_power_kw = min(original_power_kw, p_lim_kw)

            # Handle logging based on limitation state changes
            if is_limited_now:
                # Log warning if it's a new limitation or if the limited power value has changed.
                if not was_limited_previously or not np.isclose(actual_power_kw, previous_limited_power_kw):
                    logging.warning(f"{limit_reason}. Limiting power from {original_power_kw:.2f}kW to {actual_power_kw:.2f}kW")
                previous_limited_power_kw = actual_power_kw
            elif was_limited_previously:
                logging.info("Power limitation removed. Battery operating normally.")
                previous_limited_power_kw = None # Reset when not limited

            # Update state for next iteration
            was_limited_previously = is_limited_now

            # 3. Apply the actual (limited) setpoint and update SoC
            soc_kwh -= actual_power_kw * dt_h
            soc_kwh = max(0, min(capacity_kwh, soc_kwh)) # Clamp to be safe
            soc_pu = soc_kwh / capacity_kwh

            # 4. Expose actual setpoint and new SoC via Modbus
            actual_setpoint_reg = long_list_to_word([kw_to_hw(actual_power_kw)], big_endian=False)
            soc_pu_mb = int(soc_pu * 10000)

            battery_server.data_bank.set_holding_registers(config["BATTERY_SETPOINT_ACTUAL_REGISTER"], actual_setpoint_reg)
            battery_server.data_bank.set_holding_registers(config["BATTERY_SOC_REGISTER"], [soc_pu_mb])
            
            logging.debug(f"Battery: SP_orig={original_power_kw:.2f}kW, SP_act={actual_power_kw:.2f}kW, SoC={soc_kwh:.2f}kWh")

        except Exception as e:
            logging.error(f"Error in battery agent: {e}")
        
        time.sleep(max(0, dt_s - (time.time() - start_loop_time)))

    logging.info("Stopping Battery Modbus server...")
    battery_server.stop()
    logging.info("Battery Modbus server stopped.")
    logging.info("Battery agent stopped.")
