"""
Configuration loader module for HIL Scheduler.
Loads configuration from YAML file and provides it as a flat dictionary.
"""

import yaml
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_MEASUREMENT_COMPRESSION_TOLERANCES = {
    "p_setpoint_kw": 0.0,
    "battery_active_power_kw": 0.1,
    "q_setpoint_kvar": 0.0,
    "battery_reactive_power_kvar": 0.1,
    "soc_pu": 0.0001,
    "p_poi_kw": 0.1,
    "q_poi_kvar": 0.1,
    "v_poi_pu": 0.001,
}
DEFAULT_TIMEZONE_NAME = "Europe/Madrid"


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

    # Timezone settings
    time_config = yaml_config.get('time', {})
    timezone_name = time_config.get('timezone', DEFAULT_TIMEZONE_NAME)
    try:
        ZoneInfo(timezone_name)
        config['TIMEZONE_NAME'] = timezone_name
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        logging.warning(
            f"Invalid time.timezone='{timezone_name}'. "
            f"Using default '{DEFAULT_TIMEZONE_NAME}'."
        )
        config['TIMEZONE_NAME'] = DEFAULT_TIMEZONE_NAME
    config['SCHEDULE_START_TIME'] = datetime.now(ZoneInfo(config['TIMEZONE_NAME'])).replace(microsecond=0)
    
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

    # Recording settings
    recording = yaml_config.get('recording', {})
    compression = recording.get('compression', {})
    enabled_raw = compression.get('enabled', True)
    if isinstance(enabled_raw, bool):
        compression_enabled = enabled_raw
    elif isinstance(enabled_raw, str):
        compression_enabled = enabled_raw.strip().lower() in ['1', 'true', 'yes', 'on']
    else:
        compression_enabled = bool(enabled_raw)
    config['MEASUREMENT_COMPRESSION_ENABLED'] = compression_enabled

    raw_tolerances = compression.get('tolerances', {})
    tolerances = {}
    for key, default_value in DEFAULT_MEASUREMENT_COMPRESSION_TOLERANCES.items():
        raw_value = raw_tolerances.get(key, default_value)
        try:
            parsed_value = float(raw_value)
            if parsed_value < 0:
                raise ValueError("negative tolerance")
            tolerances[key] = parsed_value
        except (TypeError, ValueError):
            logging.warning(
                f"Invalid recording.compression.tolerances.{key}='{raw_value}'. "
                f"Using default {default_value}."
            )
            tolerances[key] = default_value
    config['MEASUREMENT_COMPRESSION_TOLERANCES'] = tolerances
    
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
    config['PLANT_LOCAL_NAME'] = modbus_local.get('name', 'local')
    
    # Modbus Remote Plant settings (real hardware)
    modbus_remote = yaml_config.get('modbus_remote', {})
    config['PLANT_REMOTE_MODBUS_HOST'] = modbus_remote.get('host', '10.117.133.21')
    config['PLANT_REMOTE_MODBUS_PORT'] = modbus_remote.get('port', 502)
    config['PLANT_REMOTE_NAME'] = modbus_remote.get('name', 'remote')
    
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
    raw_schedule_period_minutes = istentore_api.get('schedule_period_minutes', 15)
    try:
        schedule_period_minutes = int(raw_schedule_period_minutes)
        if schedule_period_minutes <= 0:
            raise ValueError("must be > 0")
    except (TypeError, ValueError):
        logging.warning(
            f"Invalid istentore_api.schedule_period_minutes='{raw_schedule_period_minutes}'. "
            "Using default 15."
        )
        schedule_period_minutes = 15
    config['ISTENTORE_SCHEDULE_PERIOD_MINUTES'] = schedule_period_minutes

    raw_post_measurements = istentore_api.get('post_measurements_in_api_mode', True)
    if isinstance(raw_post_measurements, bool):
        post_measurements_in_api_mode = raw_post_measurements
    elif isinstance(raw_post_measurements, str):
        post_measurements_in_api_mode = raw_post_measurements.strip().lower() in ['1', 'true', 'yes', 'on']
    else:
        post_measurements_in_api_mode = bool(raw_post_measurements)
    config['ISTENTORE_POST_MEASUREMENTS_IN_API_MODE'] = post_measurements_in_api_mode

    raw_measurement_post_period_s = istentore_api.get('measurement_post_period_s', 60)
    try:
        measurement_post_period_s = float(raw_measurement_post_period_s)
        if measurement_post_period_s <= 0:
            raise ValueError("must be > 0")
    except (TypeError, ValueError):
        logging.warning(
            f"Invalid istentore_api.measurement_post_period_s='{raw_measurement_post_period_s}'. "
            "Using default 60."
        )
        measurement_post_period_s = 60.0
    config['ISTENTORE_MEASUREMENT_POST_PERIOD_S'] = measurement_post_period_s

    raw_queue_maxlen = istentore_api.get('measurement_post_queue_maxlen', 2000)
    try:
        measurement_post_queue_maxlen = int(raw_queue_maxlen)
        if measurement_post_queue_maxlen <= 0:
            raise ValueError("must be > 0")
    except (TypeError, ValueError):
        logging.warning(
            f"Invalid istentore_api.measurement_post_queue_maxlen='{raw_queue_maxlen}'. "
            "Using default 2000."
        )
        measurement_post_queue_maxlen = 2000
    config['ISTENTORE_MEASUREMENT_POST_QUEUE_MAXLEN'] = measurement_post_queue_maxlen

    raw_retry_initial_s = istentore_api.get('measurement_post_retry_initial_s', 2)
    try:
        measurement_post_retry_initial_s = float(raw_retry_initial_s)
        if measurement_post_retry_initial_s <= 0:
            raise ValueError("must be > 0")
    except (TypeError, ValueError):
        logging.warning(
            f"Invalid istentore_api.measurement_post_retry_initial_s='{raw_retry_initial_s}'. "
            "Using default 2."
        )
        measurement_post_retry_initial_s = 2.0
    config['ISTENTORE_MEASUREMENT_POST_RETRY_INITIAL_S'] = measurement_post_retry_initial_s

    raw_retry_max_s = istentore_api.get('measurement_post_retry_max_s', 60)
    try:
        measurement_post_retry_max_s = float(raw_retry_max_s)
        if measurement_post_retry_max_s <= 0:
            raise ValueError("must be > 0")
    except (TypeError, ValueError):
        logging.warning(
            f"Invalid istentore_api.measurement_post_retry_max_s='{raw_retry_max_s}'. "
            "Using default 60."
        )
        measurement_post_retry_max_s = 60.0
    if measurement_post_retry_max_s < measurement_post_retry_initial_s:
        logging.warning(
            "Invalid retry bounds: istentore_api.measurement_post_retry_max_s is "
            "lower than measurement_post_retry_initial_s. Clamping max to initial."
        )
        measurement_post_retry_max_s = measurement_post_retry_initial_s
    config['ISTENTORE_MEASUREMENT_POST_RETRY_MAX_S'] = measurement_post_retry_max_s

    measurement_series_by_plant = istentore_api.get('measurement_series_by_plant', {})
    local_series = measurement_series_by_plant.get('local', {})
    remote_series = measurement_series_by_plant.get('remote', {})

    def _parse_measurement_series_id(raw_value, default_value, key_name):
        if raw_value is None:
            return None
        try:
            series_id = int(raw_value)
            if series_id <= 0:
                raise ValueError("must be > 0")
            return series_id
        except (TypeError, ValueError):
            logging.warning(
                f"Invalid istentore_api.measurement_series_by_plant.{key_name}='{raw_value}'. "
                f"Using default {default_value}."
            )
            return default_value

    config['ISTENTORE_MEASUREMENT_SERIES_LOCAL_SOC_ID'] = _parse_measurement_series_id(
        local_series.get('soc', 4), 4, 'local.soc'
    )
    config['ISTENTORE_MEASUREMENT_SERIES_LOCAL_P_ID'] = _parse_measurement_series_id(
        local_series.get('p', 6), 6, 'local.p'
    )
    config['ISTENTORE_MEASUREMENT_SERIES_LOCAL_Q_ID'] = _parse_measurement_series_id(
        local_series.get('q', 7), 7, 'local.q'
    )
    config['ISTENTORE_MEASUREMENT_SERIES_LOCAL_V_ID'] = _parse_measurement_series_id(
        local_series.get('v', 8), 8, 'local.v'
    )
    config['ISTENTORE_MEASUREMENT_SERIES_REMOTE_SOC_ID'] = _parse_measurement_series_id(
        remote_series.get('soc', 4), 4, 'remote.soc'
    )
    config['ISTENTORE_MEASUREMENT_SERIES_REMOTE_P_ID'] = _parse_measurement_series_id(
        remote_series.get('p', 6), 6, 'remote.p'
    )
    config['ISTENTORE_MEASUREMENT_SERIES_REMOTE_Q_ID'] = _parse_measurement_series_id(
        remote_series.get('q', 7), 7, 'remote.q'
    )
    config['ISTENTORE_MEASUREMENT_SERIES_REMOTE_V_ID'] = _parse_measurement_series_id(
        remote_series.get('v', 8), 8, 'remote.v'
    )
    
    # Startup configuration
    startup = yaml_config.get('startup', {})
    config['STARTUP_SCHEDULE_SOURCE'] = startup.get('schedule_source', 'manual')
    config['STARTUP_PLANT'] = startup.get('plant', 'local')
    
    return config
