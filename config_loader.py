"""
Configuration loader module for HIL Scheduler.
Loads configuration from YAML file and provides it as a flat dictionary.
"""

import yaml
import logging
from datetime import datetime
from pathlib import Path


def load_config(config_path="config.yaml"):
    """
    Load configuration from YAML file and return as a flat dictionary
    compatible with the existing agent interface.
    
    Args:
        config_path: Path to the YAML configuration file
        
    Returns:
        dict: Flat configuration dictionary
    """
    config_file = Path(config_path)
    
    if not config_file.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    
    with open(config_file, 'r') as f:
        yaml_config = yaml.safe_load(f)
    
    # Convert nested YAML structure to flat dictionary for backward compatibility
    config = {}
    
    # General settings
    general = yaml_config.get('general', {})
    log_level_str = general.get('log_level', 'INFO')
    config['LOG_LEVEL'] = getattr(logging, log_level_str.upper(), logging.INFO)
    config['SCHEDULE_START_TIME'] = datetime.now().replace(microsecond=0)
    
    # Schedule settings
    schedule = yaml_config.get('schedule', {})
    config['SCHEDULE_SOURCE_CSV'] = schedule.get('source_csv', 'schedule_source.csv')
    config['SCHEDULE_DURATION_H'] = schedule.get('duration_h', 0.5)
    config['SCHEDULE_DEFAULT_RESOLUTION_MIN'] = schedule.get('default_resolution_min', 5)
    
    # Timing settings
    timing = yaml_config.get('timing', {})
    config['DATA_FETCHER_PERIOD_S'] = timing.get('data_fetcher_period_s', 1)
    config['SCHEDULER_PERIOD_S'] = timing.get('scheduler_period_s', 1)
    config['PLANT_PERIOD_S'] = timing.get('plant_period_s', 5)
    config['MEASUREMENT_PERIOD_S'] = timing.get('measurement_period_s', 2)
    config['MEASUREMENTS_WRITE_PERIOD_S'] = timing.get('measurements_write_period_s', 2)
    
    # Plant settings
    plant = yaml_config.get('plant', {})
    config['PLANT_CAPACITY_KWH'] = plant.get('capacity_kwh', 50.0)
    config['PLANT_INITIAL_SOC_PU'] = plant.get('initial_soc_pu', 0.5)
    
    # Plant power limits
    power_limits = plant.get('power_limits', {})
    config['PLANT_P_MAX_KW'] = power_limits.get('p_max_kw', 1000.0)
    config['PLANT_P_MIN_KW'] = power_limits.get('p_min_kw', -1000.0)
    config['PLANT_Q_MAX_KVAR'] = power_limits.get('q_max_kvar', 600.0)
    config['PLANT_Q_MIN_KVAR'] = power_limits.get('q_min_kvar', -600.0)
    
    # POI voltage (fixed value, no impedance model)
    config['PLANT_POI_VOLTAGE_V'] = plant.get('poi_voltage_v', 20000.0)
    
    # Modbus Local Plant settings (emulated)
    modbus_local = yaml_config.get('modbus_local', {})
    config['PLANT_LOCAL_MODBUS_HOST'] = modbus_local.get('host', 'localhost')
    config['PLANT_LOCAL_MODBUS_PORT'] = modbus_local.get('port', 5020)
    
    # Modbus Remote Plant settings (real hardware)
    modbus_remote = yaml_config.get('modbus_remote', {})
    config['PLANT_REMOTE_MODBUS_HOST'] = modbus_remote.get('host', '10.117.133.21')
    config['PLANT_REMOTE_MODBUS_PORT'] = modbus_remote.get('port', 502)
    
    # Default to local plant for backward compatibility
    config['PLANT_MODBUS_HOST'] = config['PLANT_LOCAL_MODBUS_HOST']
    config['PLANT_MODBUS_PORT'] = config['PLANT_LOCAL_MODBUS_PORT']
    
    # Modbus registers - use local as default (agents will switch based on selected_plant)
    registers = modbus_local.get('registers', {})
    config['PLANT_P_SETPOINT_REGISTER'] = registers.get('p_setpoint_in', 0)
    config['PLANT_P_BATTERY_ACTUAL_REGISTER'] = registers.get('p_battery', 2)
    config['PLANT_Q_SETPOINT_REGISTER'] = registers.get('q_setpoint_in', 4)
    config['PLANT_Q_BATTERY_ACTUAL_REGISTER'] = registers.get('q_battery', 6)
    config['PLANT_ENABLE_REGISTER'] = registers.get('enable', 10)
    config['PLANT_SOC_REGISTER'] = registers.get('soc', 12)
    config['PLANT_P_POI_REGISTER'] = registers.get('p_poi', 14)
    config['PLANT_Q_POI_REGISTER'] = registers.get('q_poi', 16)
    config['PLANT_V_POI_REGISTER'] = registers.get('v_poi', 18)
    
    # Remote plant registers (can be customized independently)
    remote_registers = modbus_remote.get('registers', {})
    config['PLANT_REMOTE_P_SETPOINT_REGISTER'] = remote_registers.get('p_setpoint_in', 0)
    config['PLANT_REMOTE_P_BATTERY_ACTUAL_REGISTER'] = remote_registers.get('p_battery', 2)
    config['PLANT_REMOTE_Q_SETPOINT_REGISTER'] = remote_registers.get('q_setpoint_in', 4)
    config['PLANT_REMOTE_Q_BATTERY_ACTUAL_REGISTER'] = remote_registers.get('q_battery', 6)
    config['PLANT_REMOTE_ENABLE_REGISTER'] = remote_registers.get('enable', 10)
    config['PLANT_REMOTE_SOC_REGISTER'] = remote_registers.get('soc', 12)
    config['PLANT_REMOTE_P_POI_REGISTER'] = remote_registers.get('p_poi', 14)
    config['PLANT_REMOTE_Q_POI_REGISTER'] = remote_registers.get('q_poi', 16)
    config['PLANT_REMOTE_V_POI_REGISTER'] = remote_registers.get('v_poi', 18)
    

    # Istentore API settings
    istentore_api = yaml_config.get('istentore_api', {})
    config['ISTENTORE_BASE_URL'] = istentore_api.get('base_url', 'https://3mku48kfxf.execute-api.eu-south-2.amazonaws.com/default')
    config['ISTENTORE_EMAIL'] = istentore_api.get('email', 'i-STENTORE')
    config['ISTENTORE_POLL_INTERVAL_MIN'] = istentore_api.get('poll_interval_min', 10)
    config['ISTENTORE_POLL_START_TIME'] = istentore_api.get('poll_start_time', '17:30')
    
    # Startup configuration
    startup = yaml_config.get('startup', {})
    config['STARTUP_SCHEDULE_SOURCE'] = startup.get('schedule_source', 'manual')
    config['STARTUP_PLANT'] = startup.get('plant', 'local')
    
    return config
