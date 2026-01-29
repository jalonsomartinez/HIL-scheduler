import logging
from datetime import datetime

def configure_scheduler(remote_plant=False, remote_data=False):
    """
    Returns a configuration dictionnary for the scheduler.
    """ 
    
    if remote_plant:

        # --- Real ppc & battery ---

        CONFIG = {
            # General
            "LOG_LEVEL": logging.INFO,

            # Data Fetcher
            "SCHEDULE_SOURCE_CSV": "schedule_source.csv",
            "SCHEDULE_START_TIME": datetime.now().replace(microsecond=0),
            "SCHEDULE_DURATION_H": 0.5,
            "SCHEDULE_POWER_MIN_KW": -1000,
            "SCHEDULE_POWER_MAX_KW": 1000,
            "DATA_FETCHER_PERIOD_S": 1,

            # Scheduler
            "SCHEDULER_PERIOD_S": 1,

            # PPC Agent
            "PPC_MODBUS_HOST": "10.117.133.21",
            "PPC_MODBUS_PORT": 502,
            "PPC_PERIOD_S": 5,
            "PPC_SETPOINT_REGISTER": 86, # Es el SP en el punto de conexión
            "PPC_ENABLE_REGISTER": 1, # Es el enable para permitir escritura en la batería

            # Battery Agent

            # Si se lee del RTDS: 10.117.133.17 -> el SOC está en el HR 171, el SP del inversor en el HR 108, el feedback del SP en el inversor en el HR 111)
            # En el RTDS, el analizador estaría en el 10.117.133.25 y la medida de potencia en el pto de conexión en el HR 0
            # Los comentarios ahora se refieren al PLC
            "BATTERY_MODBUS_HOST": "10.117.133.21",
            "BATTERY_MODBUS_PORT": 502,
            "BATTERY_PERIOD_S": 5,
            "BATTERY_CAPACITY_KWH": 500.0,
            "BATTERY_INITIAL_SOC_PU": 1,
            "BATTERY_SETPOINT_IN_REGISTER": 0,
            "BATTERY_SETPOINT_ACTUAL_REGISTER": 86,
            # La medida en el POC (P_measurement_PoC) está en el HR 290 y la consigna en el pto de conexión (P_command_BESS) está en el HR 86
            # La medida de potencia a la salida de la batería (P_measurement_BESS) está en el HR 270
            "BATTERY_SOC_REGISTER": 281,

            # Measurement Agent
            "MEASUREMENT_PERIOD_S": 2,
            "MEASUREMENTS_CSV": "measurements.csv",
            "MEASUREMENTS_WRITE_PERIOD_S": 2,
        }

    else:

        # --- Local modbus servers and ppc & bat emulation ---

        CONFIG = {
            # General
            "LOG_LEVEL": logging.INFO,

            # Data Fetcher
            "SCHEDULE_SOURCE_CSV": "schedule_source.csv",
            "SCHEDULE_START_TIME": datetime.now().replace(microsecond=0),
            "SCHEDULE_DURATION_H": 0.5,
            "SCHEDULE_POWER_MIN_KW": -1000,
            "SCHEDULE_POWER_MAX_KW": 1000,
            "DATA_FETCHER_PERIOD_S": 1,

            # Scheduler
            "SCHEDULER_PERIOD_S": 1,

            # PPC Agent
            "PPC_MODBUS_HOST": "localhost",
            "PPC_MODBUS_PORT": 5020,
            "PPC_PERIOD_S": 5,
            "PPC_SETPOINT_REGISTER": 0,
            "PPC_ENABLE_REGISTER": 10,

            # Battery Agent
            "BATTERY_MODBUS_HOST": "localhost",
            "BATTERY_MODBUS_PORT": 5021,
            "BATTERY_PERIOD_S": 5,
            "BATTERY_CAPACITY_KWH": 50.0,
            "BATTERY_INITIAL_SOC_PU": 0.5,
            "BATTERY_SETPOINT_IN_REGISTER": 0,
            "BATTERY_SETPOINT_ACTUAL_REGISTER": 2,
            "BATTERY_SOC_REGISTER": 10,

            # Measurement Agent
            "MEASUREMENT_PERIOD_S": 2,
            "MEASUREMENTS_CSV": "measurements.csv",
            "MEASUREMENTS_WRITE_PERIOD_S": 2,
        }          

    return CONFIG