import glob
import logging
import math
import os
import re
import time
from collections import deque
from datetime import timedelta

import numpy as np
import pandas as pd
from pyModbusTCP.client import ModbusClient

from istentore_api import AuthenticationError, IstentoreAPI, IstentoreAPIError
from utils import hw_to_kw, uint16_to_int
from time_utils import (
    get_config_tz,
    normalize_datetime_series,
    normalize_timestamp_value,
    now_tz,
    serialize_iso_with_tz,
)


MEASUREMENT_VALUE_COLUMNS = [
    "p_setpoint_kw",
    "battery_active_power_kw",
    "q_setpoint_kvar",
    "battery_reactive_power_kvar",
    "soc_pu",
    "p_poi_kw",
    "q_poi_kvar",
    "v_poi_pu",
]
MEASUREMENT_COLUMNS = ["timestamp"] + MEASUREMENT_VALUE_COLUMNS

DEFAULT_COMPRESSION_TOLERANCES = {
    "p_setpoint_kw": 0.0,
    "battery_active_power_kw": 0.1,
    "q_setpoint_kvar": 0.0,
    "battery_reactive_power_kvar": 0.1,
    "soc_pu": 0.0001,
    "p_poi_kw": 0.1,
    "q_poi_kvar": 0.1,
    "v_poi_pu": 0.001,
}


def sanitize_plant_name(name, fallback):
    """Return filesystem-safe plant name."""
    text = str(name).strip().lower()
    text = re.sub(r"[^a-z0-9_-]+", "_", text)
    text = text.strip("_")
    return text or fallback


def normalize_measurements_df(df, tz):
    """Normalize schema/order for measurement dataframes."""
    if df is None or df.empty:
        return pd.DataFrame(columns=MEASUREMENT_COLUMNS)

    result = df.copy()
    for column in MEASUREMENT_COLUMNS:
        if column not in result.columns:
            result[column] = np.nan
    result["timestamp"] = normalize_datetime_series(result["timestamp"], tz)
    result = result.dropna(subset=["timestamp"])
    result = result[MEASUREMENT_COLUMNS].sort_values("timestamp").reset_index(drop=True)
    return result


def measurement_agent(config, shared_data):
    """
    Measurement/persistence agent.

    - Reads plant measurements at MEASUREMENT_PERIOD_S.
    - Recording is controlled only by shared_data['measurements_filename'] (None = off).
    - Writes to daily per-plant files: data/YYYYMMDD_plantname.csv
    - Buffers rows by destination file timestamp-date to handle midnight split correctly.
    - Maintains shared_data['current_file_df'] cache for selected-plant current-day plotting.
    - Posts SoC/P/Q/V to Istentore API on an independent fixed interval when API mode is active.
    """
    logging.info("Measurement agent started.")
    tz = get_config_tz(config)

    measurement_period_s = float(config.get("MEASUREMENT_PERIOD_S", 2))
    write_period_s = float(config.get("MEASUREMENTS_WRITE_PERIOD_S", 2))
    measurement_period_delta = timedelta(seconds=measurement_period_s)
    compression_enabled_raw = config.get("MEASUREMENT_COMPRESSION_ENABLED", True)
    if isinstance(compression_enabled_raw, bool):
        compression_enabled = compression_enabled_raw
    elif isinstance(compression_enabled_raw, str):
        compression_enabled = compression_enabled_raw.strip().lower() in ["1", "true", "yes", "on"]
    else:
        compression_enabled = bool(compression_enabled_raw)
    configured_tolerances = config.get("MEASUREMENT_COMPRESSION_TOLERANCES", {})
    compression_tolerances = {}
    for column in MEASUREMENT_VALUE_COLUMNS:
        raw_value = configured_tolerances.get(column, DEFAULT_COMPRESSION_TOLERANCES[column])
        try:
            parsed_value = float(raw_value)
            compression_tolerances[column] = parsed_value if parsed_value >= 0 else DEFAULT_COMPRESSION_TOLERANCES[column]
        except (TypeError, ValueError):
            compression_tolerances[column] = DEFAULT_COMPRESSION_TOLERANCES[column]

    post_measurements_enabled_raw = config.get("ISTENTORE_POST_MEASUREMENTS_IN_API_MODE", True)
    if isinstance(post_measurements_enabled_raw, bool):
        post_measurements_enabled = post_measurements_enabled_raw
    elif isinstance(post_measurements_enabled_raw, str):
        post_measurements_enabled = (
            post_measurements_enabled_raw.strip().lower() in ["1", "true", "yes", "on"]
        )
    else:
        post_measurements_enabled = bool(post_measurements_enabled_raw)

    raw_measurement_post_period_s = config.get("ISTENTORE_MEASUREMENT_POST_PERIOD_S", 60)
    try:
        measurement_post_period_s = float(raw_measurement_post_period_s)
        if measurement_post_period_s <= 0:
            raise ValueError("must be > 0")
    except (TypeError, ValueError):
        logging.warning(
            f"Measurement: Invalid ISTENTORE_MEASUREMENT_POST_PERIOD_S='{raw_measurement_post_period_s}'. "
            "Using 60 seconds."
        )
        measurement_post_period_s = 60.0

    raw_post_queue_maxlen = config.get("ISTENTORE_MEASUREMENT_POST_QUEUE_MAXLEN", 2000)
    try:
        post_queue_maxlen = int(raw_post_queue_maxlen)
        if post_queue_maxlen <= 0:
            raise ValueError("must be > 0")
    except (TypeError, ValueError):
        logging.warning(
            f"Measurement: Invalid ISTENTORE_MEASUREMENT_POST_QUEUE_MAXLEN='{raw_post_queue_maxlen}'. "
            "Using 2000."
        )
        post_queue_maxlen = 2000

    raw_post_retry_initial_s = config.get("ISTENTORE_MEASUREMENT_POST_RETRY_INITIAL_S", 2)
    try:
        post_retry_initial_s = float(raw_post_retry_initial_s)
        if post_retry_initial_s <= 0:
            raise ValueError("must be > 0")
    except (TypeError, ValueError):
        logging.warning(
            f"Measurement: Invalid ISTENTORE_MEASUREMENT_POST_RETRY_INITIAL_S='{raw_post_retry_initial_s}'. "
            "Using 2 seconds."
        )
        post_retry_initial_s = 2.0

    raw_post_retry_max_s = config.get("ISTENTORE_MEASUREMENT_POST_RETRY_MAX_S", 60)
    try:
        post_retry_max_s = float(raw_post_retry_max_s)
        if post_retry_max_s <= 0:
            raise ValueError("must be > 0")
    except (TypeError, ValueError):
        logging.warning(
            f"Measurement: Invalid ISTENTORE_MEASUREMENT_POST_RETRY_MAX_S='{raw_post_retry_max_s}'. "
            "Using 60 seconds."
        )
        post_retry_max_s = 60.0
    if post_retry_max_s < post_retry_initial_s:
        post_retry_max_s = post_retry_initial_s

    raw_plant_capacity_kwh = config.get("PLANT_CAPACITY_KWH", 0.0)
    try:
        plant_capacity_kwh = float(raw_plant_capacity_kwh)
    except (TypeError, ValueError):
        logging.warning(
            f"Measurement: Invalid PLANT_CAPACITY_KWH='{raw_plant_capacity_kwh}'. Using 0.0."
        )
        plant_capacity_kwh = 0.0

    raw_plant_poi_voltage_v = config.get("PLANT_POI_VOLTAGE_V", 20000.0)
    try:
        plant_poi_voltage_v = float(raw_plant_poi_voltage_v)
    except (TypeError, ValueError):
        logging.warning(
            f"Measurement: Invalid PLANT_POI_VOLTAGE_V='{raw_plant_poi_voltage_v}'. Using 20000.0."
        )
        plant_poi_voltage_v = 20000.0

    def normalize_series_id(value, default_value, config_key):
        if value is None:
            return None
        try:
            series_id = int(value)
            if series_id <= 0:
                raise ValueError("must be > 0")
            return series_id
        except (TypeError, ValueError):
            logging.warning(
                f"Measurement: Invalid {config_key}='{value}'. Using default {default_value}."
            )
            return default_value

    posting_series_ids = {
        "local": {
            "soc": normalize_series_id(
                config.get("ISTENTORE_MEASUREMENT_SERIES_LOCAL_SOC_ID", 4),
                4,
                "ISTENTORE_MEASUREMENT_SERIES_LOCAL_SOC_ID",
            ),
            "p": normalize_series_id(
                config.get("ISTENTORE_MEASUREMENT_SERIES_LOCAL_P_ID", 6),
                6,
                "ISTENTORE_MEASUREMENT_SERIES_LOCAL_P_ID",
            ),
            "q": normalize_series_id(
                config.get("ISTENTORE_MEASUREMENT_SERIES_LOCAL_Q_ID", 7),
                7,
                "ISTENTORE_MEASUREMENT_SERIES_LOCAL_Q_ID",
            ),
            "v": normalize_series_id(
                config.get("ISTENTORE_MEASUREMENT_SERIES_LOCAL_V_ID", 8),
                8,
                "ISTENTORE_MEASUREMENT_SERIES_LOCAL_V_ID",
            ),
        },
        "remote": {
            "soc": normalize_series_id(
                config.get("ISTENTORE_MEASUREMENT_SERIES_REMOTE_SOC_ID", 4),
                4,
                "ISTENTORE_MEASUREMENT_SERIES_REMOTE_SOC_ID",
            ),
            "p": normalize_series_id(
                config.get("ISTENTORE_MEASUREMENT_SERIES_REMOTE_P_ID", 6),
                6,
                "ISTENTORE_MEASUREMENT_SERIES_REMOTE_P_ID",
            ),
            "q": normalize_series_id(
                config.get("ISTENTORE_MEASUREMENT_SERIES_REMOTE_Q_ID", 7),
                7,
                "ISTENTORE_MEASUREMENT_SERIES_REMOTE_Q_ID",
            ),
            "v": normalize_series_id(
                config.get("ISTENTORE_MEASUREMENT_SERIES_REMOTE_V_ID", 8),
                8,
                "ISTENTORE_MEASUREMENT_SERIES_REMOTE_V_ID",
            ),
        },
    }

    for plant_name, series_cfg in posting_series_ids.items():
        disabled = [key for key, series_id in series_cfg.items() if series_id is None]
        if disabled:
            logging.info(
                f"Measurement: API posting disabled for {plant_name} series: {', '.join(disabled)}."
            )

    current_plant = None
    plant_client = None
    cache_context = None

    startup_wall_ts = normalize_timestamp_value(pd.Timestamp(now_tz(config)), tz)
    measurement_anchor_wall = startup_wall_ts.ceil("s")
    startup_monotonic = time.monotonic()
    startup_to_anchor_s = (measurement_anchor_wall - startup_wall_ts).total_seconds()
    measurement_anchor_mono = startup_monotonic + max(0.0, startup_to_anchor_s)
    last_executed_trigger_step = -1
    post_anchor_mono = measurement_anchor_mono
    last_executed_post_step = -1
    last_write_time = time.time()
    latest_measurement_for_api = None
    posting_mode_active = False
    posting_password_cached = None
    api_poster = None
    api_post_queue = deque()

    recording_active = False
    recording_plant = None
    recording_file_path = None
    awaiting_first_real_sample = False
    session_tail_ts = None
    session_tail_is_null = False
    last_real_timestamp = None
    last_real_row_by_file = {}
    run_active_by_file = {}
    compression_stats = {
        "rows_appended": 0,
        "rows_appended_as_second_point_of_run": 0,
        "rows_replaced_by_compression": 0,
        "rows_written_to_disk": 0,
        "rows_retained_as_tail": 0,
    }

    def get_plant_name(plant_type):
        if plant_type == "remote":
            return config.get("PLANT_REMOTE_NAME", "remote"), "remote"
        return config.get("PLANT_LOCAL_NAME", "local"), "local"

    def get_plant_config(plant_type):
        """Get Modbus configuration for the selected plant."""
        if plant_type == "remote":
            return {
                "host": config.get("PLANT_REMOTE_MODBUS_HOST", "10.117.133.21"),
                "port": config.get("PLANT_REMOTE_MODBUS_PORT", 502),
                "p_setpoint_reg": config.get("PLANT_REMOTE_P_SETPOINT_REGISTER", 0),
                "p_battery_reg": config.get("PLANT_REMOTE_P_BATTERY_ACTUAL_REGISTER", 2),
                "q_setpoint_reg": config.get("PLANT_REMOTE_Q_SETPOINT_REGISTER", 4),
                "q_battery_reg": config.get("PLANT_REMOTE_Q_BATTERY_ACTUAL_REGISTER", 6),
                "soc_reg": config.get("PLANT_REMOTE_SOC_REGISTER", 12),
                "p_poi_reg": config.get("PLANT_REMOTE_P_POI_REGISTER", 14),
                "q_poi_reg": config.get("PLANT_REMOTE_Q_POI_REGISTER", 16),
                "v_poi_reg": config.get("PLANT_REMOTE_V_POI_REGISTER", 18),
            }
        return {
            "host": config.get("PLANT_LOCAL_MODBUS_HOST", "localhost"),
            "port": config.get("PLANT_LOCAL_MODBUS_PORT", 5020),
            "p_setpoint_reg": config.get("PLANT_P_SETPOINT_REGISTER", 0),
            "p_battery_reg": config.get("PLANT_P_BATTERY_ACTUAL_REGISTER", 2),
            "q_setpoint_reg": config.get("PLANT_Q_SETPOINT_REGISTER", 4),
            "q_battery_reg": config.get("PLANT_Q_BATTERY_ACTUAL_REGISTER", 6),
            "soc_reg": config.get("PLANT_SOC_REGISTER", 12),
            "p_poi_reg": config.get("PLANT_P_POI_REGISTER", 14),
            "q_poi_reg": config.get("PLANT_Q_POI_REGISTER", 16),
            "v_poi_reg": config.get("PLANT_V_POI_REGISTER", 18),
        }

    def build_daily_file_path(plant_type, timestamp):
        plant_name, fallback = get_plant_name(plant_type)
        safe_name = sanitize_plant_name(plant_name, fallback)
        ts = normalize_timestamp_value(timestamp, tz)
        if pd.isna(ts):
            ts = pd.Timestamp(now_tz(config))
        date_str = ts.strftime("%Y%m%d")
        return os.path.join("data", f"{date_str}_{safe_name}.csv")

    def connect_to_plant(plant_type):
        """Create and return a new Modbus client for the specified plant."""
        nonlocal plant_client
        if plant_client is not None:
            try:
                plant_client.close()
            except Exception:
                pass

        plant_config = get_plant_config(plant_type)
        plant_client = ModbusClient(
            host=plant_config["host"],
            port=plant_config["port"],
        )
        logging.info(
            f"Measurement: Switched to {plant_type} plant at "
            f"{plant_config['host']}:{plant_config['port']}"
        )
        return plant_config

    def append_rows_to_csv(file_path, rows):
        if not rows:
            return
        try:
            os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
            df = normalize_measurements_df(pd.DataFrame(rows), tz)
            if df.empty:
                return
            write_header = (not os.path.exists(file_path)) or os.path.getsize(file_path) == 0
            df["timestamp"] = df["timestamp"].apply(lambda value: serialize_iso_with_tz(value, tz=tz))
            df.to_csv(file_path, mode="a", header=write_header, index=False)
        except Exception as e:
            raise RuntimeError(f"Error appending {len(rows)} rows to {file_path}: {e}") from e

    def upsert_row_to_current_cache_if_needed(file_path, row, replace_last=False):
        row_df = pd.DataFrame([row], columns=MEASUREMENT_COLUMNS)
        row_df["timestamp"] = normalize_datetime_series(row_df["timestamp"], tz)
        with shared_data["lock"]:
            if shared_data.get("current_file_path") != file_path:
                return
            current_df = shared_data.get("current_file_df", pd.DataFrame())
            if current_df is None or current_df.empty:
                updated = row_df
            elif replace_last:
                updated = current_df.copy()
                updated.iloc[-1] = row_df.iloc[0]
            else:
                updated = pd.concat([current_df, row_df], ignore_index=True)
            shared_data["current_file_df"] = updated
            shared_data["measurements_df"] = updated.copy()

    def enqueue_row_for_file(row, plant_type):
        target_path = build_daily_file_path(plant_type, row["timestamp"])
        append_new = False
        replace_previous = False

        row_is_real = is_real_row(row)
        prev_real_row = last_real_row_by_file.get(target_path)
        run_active = bool(run_active_by_file.get(target_path, False))

        with shared_data["lock"]:
            pending = shared_data.setdefault("pending_rows_by_file", {})
            rows = pending.setdefault(target_path, [])

            if not compression_enabled:
                rows.append(row)
                append_new = True
                compression_stats["rows_appended"] += 1
            else:
                if not row_is_real:
                    # Keep all null-boundary markers explicit and reset run state.
                    rows.append(row)
                    append_new = True
                    compression_stats["rows_appended"] += 1
                    last_real_row_by_file[target_path] = None
                    run_active_by_file[target_path] = False
                elif prev_real_row is None or not rows_are_similar(prev_real_row, row, compression_tolerances):
                    # First point of a new segment.
                    rows.append(row)
                    append_new = True
                    compression_stats["rows_appended"] += 1
                    last_real_row_by_file[target_path] = row
                    run_active_by_file[target_path] = False
                elif not run_active:
                    # Second point of stable segment -> keep both first and latest.
                    rows.append(row)
                    append_new = True
                    compression_stats["rows_appended"] += 1
                    compression_stats["rows_appended_as_second_point_of_run"] += 1
                    last_real_row_by_file[target_path] = row
                    run_active_by_file[target_path] = True
                else:
                    # Stable segment already has first+last; update only mutable tail.
                    if rows:
                        rows[-1] = row
                        replace_previous = True
                        compression_stats["rows_replaced_by_compression"] += 1
                    else:
                        # Guard: if tail is unexpectedly missing, append and continue safely.
                        rows.append(row)
                        append_new = True
                        compression_stats["rows_appended"] += 1
                    last_real_row_by_file[target_path] = row
                    run_active_by_file[target_path] = True

        if append_new or replace_previous:
            upsert_row_to_current_cache_if_needed(target_path, row, replace_last=replace_previous)
        return target_path

    def flush_pending_rows(force=False):
        nonlocal last_write_time

        rows_retained_as_tail = 0
        with shared_data["lock"]:
            pending = shared_data.get("pending_rows_by_file", {})
            if not pending:
                return
            snapshot = {}
            retained = {}
            for path, rows in pending.items():
                if not rows:
                    continue

                keep_tail = (
                    compression_enabled
                    and (not force)
                    and recording_active
                    and recording_file_path is not None
                    and path == recording_file_path
                )
                if keep_tail:
                    retained[path] = [rows[-1]]
                    rows_retained_as_tail += 1
                    if len(rows) > 1:
                        snapshot[path] = rows[:-1]
                else:
                    snapshot[path] = rows[:]

            shared_data["pending_rows_by_file"] = retained

        if rows_retained_as_tail > 0:
            compression_stats["rows_retained_as_tail"] += rows_retained_as_tail

        failed = {}
        rows_written_to_disk = 0
        for path, rows in snapshot.items():
            try:
                append_rows_to_csv(path, rows)
                rows_written_to_disk += len(rows)
            except Exception as e:
                logging.error(str(e))
                failed[path] = rows

        if failed:
            with shared_data["lock"]:
                pending = shared_data.setdefault("pending_rows_by_file", {})
                for path, rows in failed.items():
                    existing = pending.get(path, [])
                    pending[path] = rows + existing

        compression_stats["rows_written_to_disk"] += rows_written_to_disk
        if rows_written_to_disk > 0 or rows_retained_as_tail > 0:
            logging.debug(
                "Measurement compression stats: "
                f"appended={compression_stats['rows_appended']}, "
                f"second_points={compression_stats['rows_appended_as_second_point_of_run']}, "
                f"replaced={compression_stats['rows_replaced_by_compression']}, "
                f"written={compression_stats['rows_written_to_disk']}, "
                f"retained_tail={compression_stats['rows_retained_as_tail']}"
            )

        last_write_time = time.time()

    def load_file_for_cache(file_path):
        if not os.path.exists(file_path):
            return pd.DataFrame(columns=MEASUREMENT_COLUMNS)
        try:
            return normalize_measurements_df(pd.read_csv(file_path), tz)
        except Exception as e:
            logging.error(f"Error reading measurements file {file_path}: {e}")
            return pd.DataFrame(columns=MEASUREMENT_COLUMNS)

    def refresh_current_file_cache(selected_plant, now_ts):
        target_path = build_daily_file_path(selected_plant, now_ts)
        file_df = load_file_for_cache(target_path)

        with shared_data["lock"]:
            pending_rows = shared_data.get("pending_rows_by_file", {}).get(target_path, [])[:]

        if pending_rows:
            pending_df = normalize_measurements_df(pd.DataFrame(pending_rows), tz)
            file_df = normalize_measurements_df(pd.concat([file_df, pending_df], ignore_index=True), tz)

        with shared_data["lock"]:
            shared_data["current_file_path"] = target_path
            shared_data["current_file_df"] = file_df
            shared_data["measurements_df"] = file_df.copy()

        return target_path

    def is_null_row(row):
        return all(pd.isna(row.get(column)) for column in MEASUREMENT_VALUE_COLUMNS)

    def is_real_row(row):
        return not is_null_row(row)

    def rows_are_similar(prev_row, new_row, tolerances):
        for column in MEASUREMENT_VALUE_COLUMNS:
            prev_value = prev_row.get(column)
            new_value = new_row.get(column)
            if pd.isna(prev_value) or pd.isna(new_value):
                return False
            try:
                if abs(float(new_value) - float(prev_value)) > tolerances.get(column, 0.0):
                    return False
            except (TypeError, ValueError):
                return False
        return True

    def build_null_row(timestamp):
        row = {"timestamp": normalize_timestamp_value(timestamp, tz)}
        for column in MEASUREMENT_VALUE_COLUMNS:
            row[column] = np.nan
        return row

    def find_latest_row_for_plant(plant_type):
        plant_name, fallback = get_plant_name(plant_type)
        safe_name = sanitize_plant_name(plant_name, fallback)
        pattern = os.path.join("data", f"*_{safe_name}.csv")
        paths = sorted(glob.glob(pattern))

        latest_ts = None
        latest_path = None
        latest_row = None

        for path in paths:
            df = load_file_for_cache(path)
            if df.empty:
                continue
            row = df.iloc[-1].to_dict()
            ts = normalize_timestamp_value(row.get("timestamp"), tz)
            if pd.isna(ts):
                continue
            if latest_ts is None or ts > latest_ts:
                latest_ts = ts
                latest_path = path
                latest_row = row

        with shared_data["lock"]:
            pending_snapshot = {
                path: rows[:]
                for path, rows in shared_data.get("pending_rows_by_file", {}).items()
                if os.path.basename(path).endswith(f"_{safe_name}.csv")
            }

        for path, rows in pending_snapshot.items():
            for row in rows:
                ts = normalize_timestamp_value(row.get("timestamp"), tz)
                if pd.isna(ts):
                    continue
                if latest_ts is None or ts > latest_ts:
                    latest_ts = ts
                    latest_path = path
                    latest_row = row

        return latest_path, latest_row

    def sanitize_historical_tail(plant_type):
        latest_path, latest_row = find_latest_row_for_plant(plant_type)
        if latest_path is None or latest_row is None:
            return None, False

        latest_ts = normalize_timestamp_value(latest_row.get("timestamp"), tz)
        if pd.isna(latest_ts):
            return None, False

        if is_null_row(latest_row):
            return normalize_timestamp_value(latest_ts, tz), True

        null_ts = normalize_timestamp_value(latest_ts, tz) + measurement_period_delta
        null_row = build_null_row(null_ts)
        try:
            append_rows_to_csv(latest_path, [null_row])
            upsert_row_to_current_cache_if_needed(latest_path, null_row, replace_last=False)
            logging.info(
                f"Measurement: Sanitized historical tail for {plant_type} with null row at {null_ts}."
            )
            return null_ts, True
        except Exception as e:
            logging.error(f"Measurement: Failed to sanitize historical tail: {e}")
            return normalize_timestamp_value(latest_ts, tz), False

    def start_recording_session(selected_plant):
        nonlocal recording_active
        nonlocal recording_plant
        nonlocal recording_file_path
        nonlocal awaiting_first_real_sample
        nonlocal session_tail_ts
        nonlocal session_tail_is_null
        nonlocal last_real_timestamp

        recording_active = True
        recording_plant = selected_plant
        recording_file_path = build_daily_file_path(recording_plant, now_tz(config))
        awaiting_first_real_sample = True
        last_real_timestamp = None

        with shared_data["lock"]:
            shared_data["measurements_filename"] = recording_file_path

        session_tail_ts, session_tail_is_null = sanitize_historical_tail(recording_plant)
        logging.info(f"Measurement: Recording started for {recording_file_path}")

    def stop_recording_session(clear_shared_flag=True):
        nonlocal recording_active
        nonlocal recording_plant
        nonlocal recording_file_path
        nonlocal awaiting_first_real_sample
        nonlocal session_tail_ts
        nonlocal session_tail_is_null
        nonlocal last_real_timestamp

        if not recording_active:
            if clear_shared_flag:
                with shared_data["lock"]:
                    shared_data["measurements_filename"] = None
            return

        if last_real_timestamp is not None:
            null_ts = normalize_timestamp_value(last_real_timestamp, tz) + measurement_period_delta
        else:
            null_ts = pd.Timestamp(now_tz(config))

        null_row = build_null_row(null_ts)
        enqueue_row_for_file(null_row, recording_plant)
        flush_pending_rows(force=True)

        if clear_shared_flag:
            with shared_data["lock"]:
                shared_data["measurements_filename"] = None

        logging.info(
            "Measurement: Recording stopped. "
            f"Trailing null row inserted at {null_ts} and pending rows flushed."
        )

        stopped_file_path = recording_file_path

        recording_active = False
        recording_plant = None
        recording_file_path = None
        awaiting_first_real_sample = False
        session_tail_ts = None
        session_tail_is_null = False
        last_real_timestamp = None
        if stopped_file_path is not None:
            last_real_row_by_file[stopped_file_path] = None
            run_active_by_file[stopped_file_path] = False

    def ensure_api_poster(password):
        nonlocal api_poster
        nonlocal posting_password_cached

        if not password:
            api_poster = None
            posting_password_cached = None
            return None

        if api_poster is None:
            api_poster = IstentoreAPI(
                base_url=config.get("ISTENTORE_BASE_URL"),
                email=config.get("ISTENTORE_EMAIL"),
                timezone_name=config.get("TIMEZONE_NAME"),
            )
            api_poster.set_password(password)
            posting_password_cached = password
            return api_poster

        if posting_password_cached != password:
            api_poster.set_password(password)
            posting_password_cached = password

        return api_poster

    def to_utc_iso_timestamp(value):
        ts = normalize_timestamp_value(value, tz)
        if pd.isna(ts):
            return None
        return ts.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%S+00:00")

    def finite_float(value, label):
        try:
            number = float(value)
        except (TypeError, ValueError):
            logging.warning(f"Measurement: Invalid {label}='{value}' for API posting; skipping.")
            return None
        if not math.isfinite(number):
            logging.warning(f"Measurement: Non-finite {label}='{value}' for API posting; skipping.")
            return None
        return number

    def build_api_post_values(row):
        """Build per-variable API payload values using explicit target units."""
        result = {}

        soc_pu = finite_float(row.get("soc_pu"), "soc_pu")
        if soc_pu is not None:
            result["soc"] = soc_pu * plant_capacity_kwh  # kWh

        p_poi_kw = finite_float(row.get("p_poi_kw"), "p_poi_kw")
        if p_poi_kw is not None:
            result["p"] = p_poi_kw * 1000.0  # W

        q_poi_kvar = finite_float(row.get("q_poi_kvar"), "q_poi_kvar")
        if q_poi_kvar is not None:
            result["q"] = q_poi_kvar * 1000.0  # VAr

        v_poi_pu = finite_float(row.get("v_poi_pu"), "v_poi_pu")
        if v_poi_pu is not None:
            result["v"] = v_poi_pu * plant_poi_voltage_v  # V

        return result

    def enqueue_post_item(series_id, value, timestamp_iso_utc):
        if series_id is None:
            return
        if value is None:
            return
        if len(api_post_queue) >= post_queue_maxlen:
            api_post_queue.popleft()
            logging.warning(
                "Measurement: API post queue full; dropping oldest queued measurement payload."
            )
        api_post_queue.append(
            {
                "series_id": int(series_id),
                "value": float(value),
                "timestamp": timestamp_iso_utc,
                "attempt": 0,
                "next_try_mono": time.monotonic(),
            }
        )

    def enqueue_latest_measurement_posts(snapshot):
        if not snapshot:
            return

        row = snapshot.get("row")
        plant_type = snapshot.get("plant_type", "local")
        if row is None:
            return

        timestamp_iso_utc = to_utc_iso_timestamp(row.get("timestamp"))
        if timestamp_iso_utc is None:
            logging.warning("Measurement: Skipping API post because measurement timestamp is invalid.")
            return

        post_values = build_api_post_values(row)
        if not post_values:
            logging.warning("Measurement: Skipping API post due to missing/invalid measurement values.")
            return

        series_cfg = posting_series_ids.get(plant_type, posting_series_ids["local"])
        enqueue_post_item(series_cfg["soc"], post_values.get("soc"), timestamp_iso_utc)
        enqueue_post_item(series_cfg["p"], post_values.get("p"), timestamp_iso_utc)
        enqueue_post_item(series_cfg["q"], post_values.get("q"), timestamp_iso_utc)
        enqueue_post_item(series_cfg["v"], post_values.get("v"), timestamp_iso_utc)

        logging.debug(
            "Measurement: Enqueued API payloads plant=%s ts=%s "
            "soc_kwh=%s p_w=%s q_var=%s v_v=%s",
            plant_type,
            timestamp_iso_utc,
            post_values.get("soc"),
            post_values.get("p"),
            post_values.get("q"),
            post_values.get("v"),
        )

    def drain_api_post_queue(password, max_posts_per_loop=8):
        if not api_post_queue:
            return

        poster = ensure_api_poster(password)
        if poster is None:
            return

        sent = 0
        now_mono = time.monotonic()
        while api_post_queue and sent < max_posts_per_loop:
            item = api_post_queue[0]
            if item["next_try_mono"] > now_mono:
                break
            api_post_queue.popleft()
            try:
                poster.post_measurement(
                    item["series_id"],
                    item["value"],
                    timestamp=item["timestamp"],
                )
                sent += 1
            except (AuthenticationError, IstentoreAPIError, Exception) as e:
                item["attempt"] += 1
                delay_s = min(post_retry_initial_s * (2 ** (item["attempt"] - 1)), post_retry_max_s)
                item["next_try_mono"] = time.monotonic() + delay_s
                api_post_queue.append(item)
                logging.warning(
                    "Measurement: Failed to post measurement series=%s (attempt=%s), retrying in %.1fs: %s",
                    item["series_id"],
                    item["attempt"],
                    delay_s,
                    e,
                )
                break

    def take_measurement(plant_config, measurement_timestamp):
        """Take a single measurement from the plant. Returns row dict or None."""
        if plant_client is None:
            return None

        if not plant_client.is_open:
            logging.info(f"Measurement: trying to connect to {current_plant} plant Modbus server...")
            if not plant_client.open():
                logging.warning("Measurement: could not connect to plant. Retrying...")
                return None
            logging.info("Measurement: connected to plant Modbus server.")

        try:
            regs_p_setpoint = plant_client.read_holding_registers(plant_config["p_setpoint_reg"], 1)
            if not regs_p_setpoint:
                logging.warning("Measurement: could not read P setpoint.")
                return None
            p_setpoint_kw = hw_to_kw(uint16_to_int(regs_p_setpoint[0]))

            regs_p_actual = plant_client.read_holding_registers(plant_config["p_battery_reg"], 1)
            if not regs_p_actual:
                logging.warning("Measurement: could not read battery active power.")
                return None
            battery_active_power_kw = hw_to_kw(uint16_to_int(regs_p_actual[0]))

            regs_q_setpoint = plant_client.read_holding_registers(plant_config["q_setpoint_reg"], 1)
            if not regs_q_setpoint:
                logging.warning("Measurement: could not read Q setpoint.")
                return None
            q_setpoint_kvar = hw_to_kw(uint16_to_int(regs_q_setpoint[0]))

            regs_q_actual = plant_client.read_holding_registers(plant_config["q_battery_reg"], 1)
            if not regs_q_actual:
                logging.warning("Measurement: could not read battery reactive power.")
                return None
            battery_reactive_power_kvar = hw_to_kw(uint16_to_int(regs_q_actual[0]))

            regs_soc = plant_client.read_holding_registers(plant_config["soc_reg"], 1)
            if not regs_soc:
                logging.warning("Measurement: could not read SoC.")
                return None
            soc_pu = regs_soc[0] / 10000.0

            regs_p_poi = plant_client.read_holding_registers(plant_config["p_poi_reg"], 1)
            if not regs_p_poi:
                logging.warning("Measurement: could not read P_poi.")
                return None
            p_poi_kw = hw_to_kw(uint16_to_int(regs_p_poi[0]))

            regs_q_poi = plant_client.read_holding_registers(plant_config["q_poi_reg"], 1)
            if not regs_q_poi:
                logging.warning("Measurement: could not read Q_poi.")
                return None
            q_poi_kvar = hw_to_kw(uint16_to_int(regs_q_poi[0]))

            regs_v_poi = plant_client.read_holding_registers(plant_config["v_poi_reg"], 1)
            if not regs_v_poi:
                logging.warning("Measurement: could not read V_poi.")
                return None
            v_poi_pu = regs_v_poi[0] / 100.0

            normalized_measurement_timestamp = normalize_timestamp_value(measurement_timestamp, tz)
            return {
                "timestamp": normalized_measurement_timestamp,
                "p_setpoint_kw": p_setpoint_kw,
                "battery_active_power_kw": battery_active_power_kw,
                "q_setpoint_kvar": q_setpoint_kvar,
                "battery_reactive_power_kvar": battery_reactive_power_kvar,
                "soc_pu": soc_pu,
                "p_poi_kw": p_poi_kw,
                "q_poi_kvar": q_poi_kvar,
                "v_poi_pu": v_poi_pu,
            }

        except Exception as e:
            logging.error(f"Measurement: error taking measurement: {e}")
            return None

    while not shared_data["shutdown_event"].is_set():
        current_time = time.time()
        now_dt = now_tz(config)

        with shared_data["lock"]:
            selected_plant = shared_data.get("selected_plant", "local")
            requested_filename = shared_data.get("measurements_filename")
            current_cache_path = shared_data.get("current_file_path")
            active_schedule_source = shared_data.get("active_schedule_source", "manual")
            api_password = shared_data.get("api_password")

        expected_cache_path = build_daily_file_path(selected_plant, now_dt)
        new_cache_context = (selected_plant, now_dt.strftime("%Y%m%d"))
        if cache_context != new_cache_context or current_cache_path != expected_cache_path:
            refresh_current_file_cache(selected_plant, now_dt)
            cache_context = new_cache_context

        if selected_plant != current_plant:
            plant_config = connect_to_plant(selected_plant)
            current_plant = selected_plant
        else:
            plant_config = get_plant_config(current_plant or selected_plant)

        if plant_client is None:
            plant_client = ModbusClient(
                host=plant_config["host"],
                port=plant_config["port"],
            )

        if not recording_active and requested_filename is not None:
            start_recording_session(selected_plant)
        elif recording_active and requested_filename is None:
            stop_recording_session(clear_shared_flag=False)

        if recording_active:
            expected_recording_file = build_daily_file_path(recording_plant, now_dt)
            if expected_recording_file != recording_file_path:
                recording_file_path = expected_recording_file
                with shared_data["lock"]:
                    shared_data["measurements_filename"] = recording_file_path
                logging.info(f"Measurement: Midnight rollover to {recording_file_path}")

        current_step = math.floor((time.monotonic() - measurement_anchor_mono) / measurement_period_s)
        if current_step >= 0 and current_step > last_executed_trigger_step:
            last_executed_trigger_step = current_step
            scheduled_step_ts = measurement_anchor_wall + pd.Timedelta(seconds=current_step * measurement_period_s)
            measurement_row = take_measurement(plant_config, scheduled_step_ts)
            if measurement_row is not None:
                latest_measurement_for_api = {
                    "row": measurement_row.copy(),
                    "plant_type": current_plant or selected_plant,
                }
            if measurement_row is not None and recording_active:
                measurement_ts = normalize_timestamp_value(measurement_row["timestamp"], tz)
                if awaiting_first_real_sample:
                    leading_null_ts = measurement_ts - measurement_period_delta
                    already_has_boundary = (
                        session_tail_is_null
                        and session_tail_ts is not None
                        and normalize_timestamp_value(session_tail_ts, tz)
                        == normalize_timestamp_value(leading_null_ts, tz)
                    )
                    if not already_has_boundary:
                        enqueue_row_for_file(build_null_row(leading_null_ts), recording_plant)
                    awaiting_first_real_sample = False

                enqueue_row_for_file(measurement_row, recording_plant)
                last_real_timestamp = measurement_ts
                session_tail_ts = measurement_ts
                session_tail_is_null = False

        posting_mode_now = (
            post_measurements_enabled
            and active_schedule_source == "api"
            and bool(api_password)
        )
        if not posting_mode_now and posting_mode_active:
            if api_post_queue:
                api_post_queue.clear()
                logging.info("Measurement: API posting disabled, clearing pending API post queue.")
            ensure_api_poster(None)
        posting_mode_active = posting_mode_now

        current_post_step = math.floor((time.monotonic() - post_anchor_mono) / measurement_post_period_s)
        if posting_mode_active and current_post_step >= 0 and current_post_step > last_executed_post_step:
            last_executed_post_step = current_post_step
            enqueue_latest_measurement_posts(latest_measurement_for_api)

        if posting_mode_active:
            drain_api_post_queue(api_password)

        if current_time - last_write_time >= write_period_s:
            flush_pending_rows(force=False)

        time.sleep(0.1)

    logging.info("Measurement agent stopping.")
    if recording_active:
        stop_recording_session(clear_shared_flag=False)
    flush_pending_rows(force=True)
    try:
        if plant_client is not None:
            plant_client.close()
    except Exception:
        pass
    logging.info("Measurement agent stopped.")
