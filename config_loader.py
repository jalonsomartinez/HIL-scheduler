"""Configuration loader for HIL Scheduler."""

import logging
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

from modbus.codec import format_meta
from modbus.units import validate_point_unit
from runtime.defaults import (
    DEFAULT_MEASUREMENT_COMPRESSION_MAX_KEPT_GAP_S,
    DEFAULT_MEASUREMENT_COMPRESSION_TOLERANCES,
    DEFAULT_TIMEZONE_NAME,
)
from runtime.parsing import parse_bool

DEFAULT_STARTUP_INITIAL_SOC_PU = 0.5
DEFAULT_DASHBOARD_PRIVATE_HOST = "127.0.0.1"
DEFAULT_DASHBOARD_PRIVATE_PORT = 8050
DEFAULT_DASHBOARD_PUBLIC_READONLY_ENABLED = False
DEFAULT_DASHBOARD_PUBLIC_READONLY_HOST = "127.0.0.1"
DEFAULT_DASHBOARD_PUBLIC_READONLY_PORT = 8060
DEFAULT_DASHBOARD_PUBLIC_READONLY_AUTH_MODE = "basic"
DEFAULT_MODEL = {
    "capacity_kwh": 50.0,
    "power_limits": {
        "p_max_kw": 1000.0,
        "p_min_kw": -1000.0,
        "q_max_kvar": 600.0,
        "q_min_kvar": -600.0,
    },
    "poi_voltage_kv": 20.0,
}
LEGACY_ALIAS_ENV_VAR = "HIL_ENABLE_LEGACY_CONFIG_ALIASES"
MODBUS_BYTE_ORDERS = {"big", "little"}
MODBUS_WORD_ORDERS = {"msw_first", "lsw_first"}
MODBUS_POINT_FORMATS = {"int16", "uint16", "int32", "uint32", "float32"}
MODBUS_POINT_ACCESS_VALUES = {"r", "w", "rw"}
REQUIRED_MODBUS_POINT_NAMES = (
    "p_setpoint",
    "p_battery",
    "q_setpoint",
    "q_battery",
    "enable",
    "soc",
    "p_poi",
    "q_poi",
    "v_poi",
)


def _parse_bool(value, default):
    return parse_bool(value, default)


def _parse_float(value, default, key_name, min_value=None):
    try:
        result = float(value)
        if min_value is not None and result < min_value:
            raise ValueError("below minimum")
        return result
    except (TypeError, ValueError):
        logging.warning("Invalid %s='%s'. Using default %s.", key_name, value, default)
        return default


def _parse_int(value, default, key_name, min_value=None):
    try:
        result = int(value)
        if min_value is not None and result < min_value:
            raise ValueError("below minimum")
        return result
    except (TypeError, ValueError):
        logging.warning("Invalid %s='%s'. Using default %s.", key_name, value, default)
        return default


def _parse_timezone(timezone_name):
    try:
        ZoneInfo(timezone_name)
        return timezone_name
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        logging.warning(
            "Invalid time.timezone='%s'. Using default '%s'.",
            timezone_name,
            DEFAULT_TIMEZONE_NAME,
        )
        return DEFAULT_TIMEZONE_NAME


def _parse_hhmm_required(value, default, key_name):
    if value is None:
        value = default

    text = str(value).strip()
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if not match:
        raise ValueError(f"Invalid {key_name}='{value}'. Expected HH:MM (24-hour clock).")

    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError(f"Invalid {key_name}='{value}'. Expected HH:MM (24-hour clock).")

    return f"{hour:02d}:{minute:02d}"


def _parse_choice_required(value, allowed_values, key_name):
    if value is None:
        raise ValueError(f"Missing required config key '{key_name}'.")
    normalized = str(value).strip().lower()
    if normalized not in allowed_values:
        allowed_text = ", ".join(sorted(allowed_values))
        raise ValueError(f"Invalid {key_name}='{value}'. Allowed values: {allowed_text}.")
    return normalized


def _parse_choice(value, allowed_values, default, key_name):
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized not in allowed_values:
        allowed_text = ", ".join(sorted(allowed_values))
        logging.warning(
            "Invalid %s='%s'. Using default '%s'. Allowed values: %s.",
            key_name,
            value,
            default,
            allowed_text,
        )
        return default
    return normalized


def _parse_host(value, default, key_name):
    if value is None:
        return default
    host = str(value).strip()
    if not host:
        logging.warning("Invalid %s='%s'. Using default '%s'.", key_name, value, default)
        return default
    return host


def _normalize_modbus_point(point_name, raw_point, prefix):
    if not isinstance(raw_point, dict):
        raise ValueError(f"Invalid {prefix}.points.{point_name}: expected mapping.")
    if "register_type" in raw_point:
        raise ValueError(
            f"Invalid {prefix}.points.{point_name}.register_type: holding registers are the only supported type "
            "and register_type must be omitted."
        )
    if "byte_order" in raw_point or "word_order" in raw_point:
        raise ValueError(
            f"Invalid {prefix}.points.{point_name}: byte_order/word_order must be defined at endpoint level."
        )

    address = _parse_int(raw_point.get("address"), 0, f"{prefix}.points.{point_name}.address", min_value=0)
    if "address" not in raw_point:
        raise ValueError(f"Missing required config key '{prefix}.points.{point_name}.address'.")

    format_name = _parse_choice_required(
        raw_point.get("format"),
        MODBUS_POINT_FORMATS,
        f"{prefix}.points.{point_name}.format",
    )
    access = _parse_choice_required(
        raw_point.get("access"),
        MODBUS_POINT_ACCESS_VALUES,
        f"{prefix}.points.{point_name}.access",
    )

    if "unit" not in raw_point:
        raise ValueError(f"Missing required config key '{prefix}.points.{point_name}.unit'.")
    unit = str(raw_point.get("unit"))
    if not unit.strip():
        raise ValueError(f"Invalid {prefix}.points.{point_name}.unit: must be non-empty.")
    unit = validate_point_unit(point_name, unit)

    if "eng_per_count" not in raw_point:
        raise ValueError(f"Missing required config key '{prefix}.points.{point_name}.eng_per_count'.")
    eng_per_count = _parse_float(
        raw_point.get("eng_per_count"),
        1.0,
        f"{prefix}.points.{point_name}.eng_per_count",
    )
    if eng_per_count <= 0.0:
        raise ValueError(f"Invalid {prefix}.points.{point_name}.eng_per_count='{eng_per_count}'. Must be > 0.")

    meta = format_meta(format_name)
    return {
        "name": str(point_name),
        "address": int(address),
        "format": format_name,
        "word_count": int(meta["word_count"]),
        "byte_count": int(meta["byte_count"]),
        "access": access,
        "unit": unit,
        "eng_per_count": float(eng_per_count),
    }


def _normalize_points(raw_points, prefix):
    if raw_points is None:
        raise ValueError(f"Missing required config key '{prefix}.points'.")
    if not isinstance(raw_points, dict):
        raise ValueError(f"Invalid {prefix}.points: expected mapping.")

    missing = [name for name in REQUIRED_MODBUS_POINT_NAMES if name not in raw_points]
    if missing:
        raise ValueError(f"Missing required Modbus points at {prefix}.points: {', '.join(missing)}")

    points = {}
    for point_name, raw_point in raw_points.items():
        points[str(point_name)] = _normalize_modbus_point(str(point_name), raw_point, prefix)
    return points


def _normalize_model(raw_model, prefix):
    power_limits_raw = raw_model.get("power_limits", {})
    defaults = DEFAULT_MODEL["power_limits"]
    if "poi_voltage_v" in raw_model:
        raise ValueError(
            f"Config key '{prefix}.model.poi_voltage_v' is no longer supported. "
            f"Use '{prefix}.model.poi_voltage_kv'."
        )
    model = {
        "capacity_kwh": _parse_float(
            raw_model.get("capacity_kwh", DEFAULT_MODEL["capacity_kwh"]),
            DEFAULT_MODEL["capacity_kwh"],
            f"{prefix}.model.capacity_kwh",
            min_value=0.0,
        ),
        "power_limits": {
            "p_max_kw": _parse_float(
                power_limits_raw.get("p_max_kw", defaults["p_max_kw"]),
                defaults["p_max_kw"],
                f"{prefix}.model.power_limits.p_max_kw",
            ),
            "p_min_kw": _parse_float(
                power_limits_raw.get("p_min_kw", defaults["p_min_kw"]),
                defaults["p_min_kw"],
                f"{prefix}.model.power_limits.p_min_kw",
            ),
            "q_max_kvar": _parse_float(
                power_limits_raw.get("q_max_kvar", defaults["q_max_kvar"]),
                defaults["q_max_kvar"],
                f"{prefix}.model.power_limits.q_max_kvar",
            ),
            "q_min_kvar": _parse_float(
                power_limits_raw.get("q_min_kvar", defaults["q_min_kvar"]),
                defaults["q_min_kvar"],
                f"{prefix}.model.power_limits.q_min_kvar",
            ),
        },
        "poi_voltage_kv": _parse_float(
            raw_model.get("poi_voltage_kv", DEFAULT_MODEL["poi_voltage_kv"]),
            DEFAULT_MODEL["poi_voltage_kv"],
            f"{prefix}.model.poi_voltage_kv",
            min_value=0.0,
        ),
    }
    return model


def _normalize_series(raw_series, prefix, defaults):
    result = {}
    for key, default in defaults.items():
        value = raw_series.get(key, default)
        if value is None:
            result[key] = None
            continue
        result[key] = _parse_int(value, default, f"{prefix}.measurement_series.{key}", min_value=1)
    return result


def _normalize_transport_endpoint(raw_endpoint, prefix, default_host, default_port):
    if "registers" in raw_endpoint:
        raise ValueError(
            f"Config key '{prefix}.registers' is no longer supported. "
            f"Use '{prefix}.points' with structured point definitions."
        )

    endpoint = {
        "host": str(raw_endpoint.get("host", default_host)),
        "port": _parse_int(raw_endpoint.get("port", default_port), default_port, f"{prefix}.port", min_value=1),
        "byte_order": _parse_choice_required(raw_endpoint.get("byte_order"), MODBUS_BYTE_ORDERS, f"{prefix}.byte_order"),
        "word_order": _parse_choice_required(raw_endpoint.get("word_order"), MODBUS_WORD_ORDERS, f"{prefix}.word_order"),
        "points": _normalize_points(raw_endpoint.get("points"), prefix),
    }
    return endpoint


def _normalize_plants_new_schema(yaml_config):
    plants_raw = yaml_config.get("plants", {})
    defaults_by_plant = {
        "lib": {"soc": 4, "p": 6, "q": 7, "v": 8},
        "vrfb": {"soc": 5, "p": 11, "q": 10, "v": 9},
    }
    plants = {}

    for plant_id in ("lib", "vrfb"):
        raw = plants_raw.get(plant_id, {})
        if not raw:
            logging.warning("Missing plants.%s section. Using defaults.", plant_id)

        model = _normalize_model(raw.get("model", {}), f"plants.{plant_id}")
        modbus_raw = raw.get("modbus", {})

        local_endpoint = _normalize_transport_endpoint(
            modbus_raw.get("local", {}),
            f"plants.{plant_id}.modbus.local",
            "localhost",
            5020 if plant_id == "lib" else 5021,
        )
        remote_endpoint = _normalize_transport_endpoint(
            modbus_raw.get("remote", {}),
            f"plants.{plant_id}.modbus.remote",
            "10.117.133.21" if plant_id == "lib" else "10.117.133.22",
            502,
        )

        plants[plant_id] = {
            "id": plant_id,
            "name": str(raw.get("name", plant_id.upper())),
            "model": model,
            "modbus": {
                "local": local_endpoint,
                "remote": remote_endpoint,
            },
            "measurement_series": _normalize_series(
                raw.get("measurement_series", {}),
                f"plants.{plant_id}",
                defaults_by_plant[plant_id],
            ),
        }

    return plants


def _build_legacy_plants(yaml_config):
    logging.warning(
        "Using legacy config schema. Please migrate to 'plants.lib'/'plants.vrfb' with startup.transport_mode."
    )

    legacy_model = _normalize_model(yaml_config.get("plant", {}), "plant")
    modbus_local = yaml_config.get("modbus_local", {})
    modbus_remote = yaml_config.get("modbus_remote", {})

    local_ep = _normalize_transport_endpoint(modbus_local, "modbus_local", "localhost", 5020)
    remote_ep = _normalize_transport_endpoint(modbus_remote, "modbus_remote", "10.117.133.21", 502)

    series_root = yaml_config.get("istentore_api", {}).get("measurement_series_by_plant", {})
    lib_series = _normalize_series(series_root.get("local", {}), "istentore_api.measurement_series_by_plant.local", {"soc": 4, "p": 6, "q": 7, "v": 8})
    vrfb_series = _normalize_series(series_root.get("remote", {}), "istentore_api.measurement_series_by_plant.remote", {"soc": 5, "p": 11, "q": 10, "v": 9})

    # Legacy had transport endpoints, not logical plant endpoints.
    # For one-release compatibility we map:
    # - LIB local/remote from legacy local/remote
    # - VRFB local duplicated from legacy local with port fallback 5021
    # - VRFB remote duplicated from legacy remote
    vrfb_local = {
        "host": local_ep["host"],
        "port": local_ep["port"] + 1 if local_ep["port"] == 5020 else local_ep["port"],
        "byte_order": local_ep["byte_order"],
        "word_order": local_ep["word_order"],
        "points": dict(local_ep["points"]),
    }
    vrfb_remote = {
        "host": remote_ep["host"],
        "port": remote_ep["port"],
        "byte_order": remote_ep["byte_order"],
        "word_order": remote_ep["word_order"],
        "points": dict(remote_ep["points"]),
    }

    return {
        "lib": {
            "id": "lib",
            "name": str(modbus_local.get("name", "LIB")),
            "model": dict(legacy_model),
            "modbus": {"local": local_ep, "remote": remote_ep},
            "measurement_series": lib_series,
        },
        "vrfb": {
            "id": "vrfb",
            "name": "VRFB",
            "model": dict(legacy_model),
            "modbus": {"local": vrfb_local, "remote": vrfb_remote},
            "measurement_series": vrfb_series,
        },
    }


def _set_legacy_flat_keys(config, plants, startup_initial_soc_pu):
    lib = plants["lib"]
    vrfb = plants["vrfb"]

    lib_local = lib["modbus"]["local"]
    lib_remote = lib["modbus"]["remote"]

    config["PLANT_CAPACITY_KWH"] = lib["model"]["capacity_kwh"]
    config["PLANT_INITIAL_SOC_PU"] = startup_initial_soc_pu
    config["PLANT_P_MAX_KW"] = lib["model"]["power_limits"]["p_max_kw"]
    config["PLANT_P_MIN_KW"] = lib["model"]["power_limits"]["p_min_kw"]
    config["PLANT_Q_MAX_KVAR"] = lib["model"]["power_limits"]["q_max_kvar"]
    config["PLANT_Q_MIN_KVAR"] = lib["model"]["power_limits"]["q_min_kvar"]
    config["PLANT_POI_VOLTAGE_V"] = lib["model"]["poi_voltage_kv"] * 1000.0

    config["PLANT_LOCAL_NAME"] = lib["name"]
    config["PLANT_REMOTE_NAME"] = vrfb["name"]

    config["PLANT_LOCAL_MODBUS_HOST"] = lib_local["host"]
    config["PLANT_LOCAL_MODBUS_PORT"] = lib_local["port"]
    config["PLANT_REMOTE_MODBUS_HOST"] = lib_remote["host"]
    config["PLANT_REMOTE_MODBUS_PORT"] = lib_remote["port"]

    for key, point in lib_local["points"].items():
        value = int(point["address"])
        if key == "p_setpoint":
            config["PLANT_P_SETPOINT_REGISTER"] = value
        elif key == "p_battery":
            config["PLANT_P_BATTERY_ACTUAL_REGISTER"] = value
        elif key == "q_setpoint":
            config["PLANT_Q_SETPOINT_REGISTER"] = value
        elif key == "q_battery":
            config["PLANT_Q_BATTERY_ACTUAL_REGISTER"] = value
        elif key == "enable":
            config["PLANT_ENABLE_REGISTER"] = value
        elif key == "soc":
            config["PLANT_SOC_REGISTER"] = value
        elif key == "p_poi":
            config["PLANT_P_POI_REGISTER"] = value
        elif key == "q_poi":
            config["PLANT_Q_POI_REGISTER"] = value
        elif key == "v_poi":
            config["PLANT_V_POI_REGISTER"] = value

    for key, point in lib_remote["points"].items():
        value = int(point["address"])
        if key == "p_setpoint":
            config["PLANT_REMOTE_P_SETPOINT_REGISTER"] = value
        elif key == "p_battery":
            config["PLANT_REMOTE_P_BATTERY_ACTUAL_REGISTER"] = value
        elif key == "q_setpoint":
            config["PLANT_REMOTE_Q_SETPOINT_REGISTER"] = value
        elif key == "q_battery":
            config["PLANT_REMOTE_Q_BATTERY_ACTUAL_REGISTER"] = value
        elif key == "enable":
            config["PLANT_REMOTE_ENABLE_REGISTER"] = value
        elif key == "soc":
            config["PLANT_REMOTE_SOC_REGISTER"] = value
        elif key == "p_poi":
            config["PLANT_REMOTE_P_POI_REGISTER"] = value
        elif key == "q_poi":
            config["PLANT_REMOTE_Q_POI_REGISTER"] = value
        elif key == "v_poi":
            config["PLANT_REMOTE_V_POI_REGISTER"] = value


def _legacy_aliases_enabled():
    raw = os.getenv(LEGACY_ALIAS_ENV_VAR, "0")
    return _parse_bool(raw, False)


def load_config(config_path="config.yaml"):
    """Load configuration from YAML and return validated runtime dict."""
    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(config_file, "r", encoding="utf-8") as handle:
        yaml_config = yaml.safe_load(handle) or {}

    config = {}

    general = yaml_config.get("general", {})
    log_level_str = str(general.get("log_level", "INFO")).upper()
    config["LOG_LEVEL"] = getattr(logging, log_level_str, logging.INFO)

    time_cfg = yaml_config.get("time", {})
    config["TIMEZONE_NAME"] = _parse_timezone(time_cfg.get("timezone", DEFAULT_TIMEZONE_NAME))
    config["SCHEDULE_START_TIME"] = datetime.now(ZoneInfo(config["TIMEZONE_NAME"])).replace(microsecond=0)

    schedule_cfg = yaml_config.get("schedule", {})
    config["SCHEDULE_SOURCE_CSV"] = schedule_cfg.get("source_csv", "schedule_source.csv")
    config["SCHEDULE_DURATION_H"] = _parse_float(schedule_cfg.get("duration_h", 0.5), 0.5, "schedule.duration_h")
    config["SCHEDULE_DEFAULT_RESOLUTION_MIN"] = _parse_int(
        schedule_cfg.get("default_resolution_min", 5),
        5,
        "schedule.default_resolution_min",
        min_value=1,
    )

    timing_cfg = yaml_config.get("timing", {})
    config["DATA_FETCHER_PERIOD_S"] = _parse_float(
        timing_cfg.get("data_fetcher_period_s", 120), 120, "timing.data_fetcher_period_s", min_value=0.1
    )
    config["SCHEDULER_PERIOD_S"] = _parse_float(
        timing_cfg.get("scheduler_period_s", 1), 1, "timing.scheduler_period_s", min_value=0.1
    )
    config["PLANT_PERIOD_S"] = _parse_float(
        timing_cfg.get("plant_period_s", 1), 1, "timing.plant_period_s", min_value=0.1
    )
    config["MEASUREMENT_PERIOD_S"] = _parse_float(
        timing_cfg.get("measurement_period_s", 5), 5, "timing.measurement_period_s", min_value=0.1
    )
    config["MEASUREMENTS_WRITE_PERIOD_S"] = _parse_float(
        timing_cfg.get("measurements_write_period_s", 60),
        60,
        "timing.measurements_write_period_s",
        min_value=0.1,
    )

    dashboard_cfg = yaml_config.get("dashboard", {})
    dashboard_private_cfg = dashboard_cfg.get("private", {})
    dashboard_public_cfg = dashboard_cfg.get("public_readonly", {})
    dashboard_public_auth_cfg = dashboard_public_cfg.get("auth", {})

    config["DASHBOARD_PRIVATE_HOST"] = _parse_host(
        dashboard_private_cfg.get("host", DEFAULT_DASHBOARD_PRIVATE_HOST),
        DEFAULT_DASHBOARD_PRIVATE_HOST,
        "dashboard.private.host",
    )
    config["DASHBOARD_PRIVATE_PORT"] = _parse_int(
        dashboard_private_cfg.get("port", DEFAULT_DASHBOARD_PRIVATE_PORT),
        DEFAULT_DASHBOARD_PRIVATE_PORT,
        "dashboard.private.port",
        min_value=1,
    )
    config["DASHBOARD_PUBLIC_READONLY_ENABLED"] = _parse_bool(
        dashboard_public_cfg.get("enabled", DEFAULT_DASHBOARD_PUBLIC_READONLY_ENABLED),
        DEFAULT_DASHBOARD_PUBLIC_READONLY_ENABLED,
    )
    config["DASHBOARD_PUBLIC_READONLY_HOST"] = _parse_host(
        dashboard_public_cfg.get("host", DEFAULT_DASHBOARD_PUBLIC_READONLY_HOST),
        DEFAULT_DASHBOARD_PUBLIC_READONLY_HOST,
        "dashboard.public_readonly.host",
    )
    config["DASHBOARD_PUBLIC_READONLY_PORT"] = _parse_int(
        dashboard_public_cfg.get("port", DEFAULT_DASHBOARD_PUBLIC_READONLY_PORT),
        DEFAULT_DASHBOARD_PUBLIC_READONLY_PORT,
        "dashboard.public_readonly.port",
        min_value=1,
    )
    config["DASHBOARD_PUBLIC_READONLY_AUTH_MODE"] = _parse_choice(
        dashboard_public_auth_cfg.get("mode", DEFAULT_DASHBOARD_PUBLIC_READONLY_AUTH_MODE),
        {"basic", "none"},
        DEFAULT_DASHBOARD_PUBLIC_READONLY_AUTH_MODE,
        "dashboard.public_readonly.auth.mode",
    )

    recording_cfg = yaml_config.get("recording", {})
    compression_cfg = recording_cfg.get("compression", {})
    config["MEASUREMENT_COMPRESSION_ENABLED"] = _parse_bool(compression_cfg.get("enabled", True), True)
    config["MEASUREMENT_COMPRESSION_MAX_KEPT_GAP_S"] = _parse_float(
        compression_cfg.get("max_kept_gap_s", DEFAULT_MEASUREMENT_COMPRESSION_MAX_KEPT_GAP_S),
        DEFAULT_MEASUREMENT_COMPRESSION_MAX_KEPT_GAP_S,
        "recording.compression.max_kept_gap_s",
        min_value=0.0,
    )

    tolerances_cfg = compression_cfg.get("tolerances", {})
    if "v_poi_pu" in tolerances_cfg:
        raise ValueError(
            "Config key 'recording.compression.tolerances.v_poi_pu' is no longer supported. "
            "Use 'recording.compression.tolerances.v_poi_kV'."
        )
    tolerances = {}
    for key, default_value in DEFAULT_MEASUREMENT_COMPRESSION_TOLERANCES.items():
        tolerances[key] = _parse_float(
            tolerances_cfg.get(key, default_value),
            default_value,
            f"recording.compression.tolerances.{key}",
            min_value=0.0,
        )
    config["MEASUREMENT_COMPRESSION_TOLERANCES"] = tolerances

    api_cfg = yaml_config.get("istentore_api", {})
    config["ISTENTORE_BASE_URL"] = api_cfg.get("base_url", "https://3mku48kfxf.execute-api.eu-south-2.amazonaws.com/default")
    config["ISTENTORE_EMAIL"] = api_cfg.get("email", "i-STENTORE")
    if "poll_start_time" in api_cfg:
        raise ValueError(
            "Config key 'istentore_api.poll_start_time' was renamed to "
            "'istentore_api.tomorrow_poll_start_time'. Update config.yaml."
        )
    config["ISTENTORE_TOMORROW_POLL_START_TIME"] = _parse_hhmm_required(
        api_cfg.get("tomorrow_poll_start_time", "17:30"),
        "17:30",
        "istentore_api.tomorrow_poll_start_time",
    )
    config["ISTENTORE_SCHEDULE_PERIOD_MINUTES"] = _parse_int(
        api_cfg.get("schedule_period_minutes", 15),
        15,
        "istentore_api.schedule_period_minutes",
        min_value=1,
    )
    config["ISTENTORE_POST_MEASUREMENTS_IN_API_MODE"] = _parse_bool(
        api_cfg.get("post_measurements_in_api_mode", True),
        True,
    )
    config["ISTENTORE_MEASUREMENT_POST_PERIOD_S"] = _parse_float(
        api_cfg.get("measurement_post_period_s", 60),
        60,
        "istentore_api.measurement_post_period_s",
        min_value=0.1,
    )
    config["ISTENTORE_MEASUREMENT_POST_QUEUE_MAXLEN"] = _parse_int(
        api_cfg.get("measurement_post_queue_maxlen", 2000),
        2000,
        "istentore_api.measurement_post_queue_maxlen",
        min_value=1,
    )
    config["ISTENTORE_MEASUREMENT_POST_RETRY_INITIAL_S"] = _parse_float(
        api_cfg.get("measurement_post_retry_initial_s", 2),
        2,
        "istentore_api.measurement_post_retry_initial_s",
        min_value=0.1,
    )
    config["ISTENTORE_MEASUREMENT_POST_RETRY_MAX_S"] = _parse_float(
        api_cfg.get("measurement_post_retry_max_s", 60),
        60,
        "istentore_api.measurement_post_retry_max_s",
        min_value=0.1,
    )
    if config["ISTENTORE_MEASUREMENT_POST_RETRY_MAX_S"] < config["ISTENTORE_MEASUREMENT_POST_RETRY_INITIAL_S"]:
        config["ISTENTORE_MEASUREMENT_POST_RETRY_MAX_S"] = config["ISTENTORE_MEASUREMENT_POST_RETRY_INITIAL_S"]

    if "plants" in yaml_config:
        plants = _normalize_plants_new_schema(yaml_config)
    else:
        raise ValueError(
            "Legacy top-level config schema is no longer supported. "
            "Migrate to 'plants.*.modbus.{local,remote}.points' and add endpoint "
            "'byte_order'/'word_order' fields."
        )

    config["PLANTS"] = plants
    config["PLANT_IDS"] = tuple(["lib", "vrfb"])

    startup_cfg = yaml_config.get("startup", {})
    transport_mode_raw = startup_cfg.get("transport_mode", startup_cfg.get("plant", "local"))
    transport_mode = str(transport_mode_raw).strip().lower()
    if transport_mode not in ["local", "remote"]:
        logging.warning("Invalid startup.transport_mode='%s'. Using 'local'.", transport_mode_raw)
        transport_mode = "local"

    schedule_source = str(startup_cfg.get("schedule_source", "manual")).strip().lower()
    if schedule_source not in ["manual", "api"]:
        logging.warning("Invalid startup.schedule_source='%s'. Using 'manual'.", schedule_source)
        schedule_source = "manual"

    config["STARTUP_INITIAL_SOC_PU"] = _parse_float(
        startup_cfg.get("initial_soc_pu", DEFAULT_STARTUP_INITIAL_SOC_PU),
        DEFAULT_STARTUP_INITIAL_SOC_PU,
        "startup.initial_soc_pu",
    )
    config["STARTUP_TRANSPORT_MODE"] = transport_mode
    config["STARTUP_SCHEDULE_SOURCE"] = schedule_source

    # Legacy aliases are intentionally opt-in and only for temporary migration.
    if _legacy_aliases_enabled():
        logging.warning(
            "Legacy config aliases enabled via %s. This compatibility mode is deprecated.",
            LEGACY_ALIAS_ENV_VAR,
        )
        config["TRANSPORT_MODE"] = transport_mode
        config["STARTUP_PLANT"] = transport_mode
        _set_legacy_flat_keys(config, plants, config["STARTUP_INITIAL_SOC_PU"])

        config["ISTENTORE_MEASUREMENT_SERIES_LOCAL_SOC_ID"] = plants["lib"]["measurement_series"]["soc"]
        config["ISTENTORE_MEASUREMENT_SERIES_LOCAL_P_ID"] = plants["lib"]["measurement_series"]["p"]
        config["ISTENTORE_MEASUREMENT_SERIES_LOCAL_Q_ID"] = plants["lib"]["measurement_series"]["q"]
        config["ISTENTORE_MEASUREMENT_SERIES_LOCAL_V_ID"] = plants["lib"]["measurement_series"]["v"]
        config["ISTENTORE_MEASUREMENT_SERIES_REMOTE_SOC_ID"] = plants["vrfb"]["measurement_series"]["soc"]
        config["ISTENTORE_MEASUREMENT_SERIES_REMOTE_P_ID"] = plants["vrfb"]["measurement_series"]["p"]
        config["ISTENTORE_MEASUREMENT_SERIES_REMOTE_Q_ID"] = plants["vrfb"]["measurement_series"]["q"]
        config["ISTENTORE_MEASUREMENT_SERIES_REMOTE_V_ID"] = plants["vrfb"]["measurement_series"]["v"]

    return config
