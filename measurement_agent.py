import glob
import logging
import os
import re
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from pyModbusTCP.client import ModbusClient

from utils import hw_to_kw, uint16_to_int


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


def normalize_measurements_df(df):
    """Normalize schema/order for measurement dataframes."""
    if df is None or df.empty:
        return pd.DataFrame(columns=MEASUREMENT_COLUMNS)

    result = df.copy()
    for column in MEASUREMENT_COLUMNS:
        if column not in result.columns:
            result[column] = np.nan
    result["timestamp"] = pd.to_datetime(result["timestamp"], errors="coerce")
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
    """
    logging.info("Measurement agent started.")

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

    current_plant = None
    plant_client = None
    cache_context = None

    last_measurement_time = time.time()
    last_write_time = time.time()

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
        date_str = pd.Timestamp(timestamp).strftime("%Y%m%d")
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
            df = normalize_measurements_df(pd.DataFrame(rows))
            if df.empty:
                return
            write_header = (not os.path.exists(file_path)) or os.path.getsize(file_path) == 0
            df.to_csv(file_path, mode="a", header=write_header, index=False)
        except Exception as e:
            raise RuntimeError(f"Error appending {len(rows)} rows to {file_path}: {e}") from e

    def upsert_row_to_current_cache_if_needed(file_path, row, replace_last=False):
        row_df = pd.DataFrame([row], columns=MEASUREMENT_COLUMNS)
        row_df["timestamp"] = pd.to_datetime(row_df["timestamp"], errors="coerce")
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
            return normalize_measurements_df(pd.read_csv(file_path))
        except Exception as e:
            logging.error(f"Error reading measurements file {file_path}: {e}")
            return pd.DataFrame(columns=MEASUREMENT_COLUMNS)

    def refresh_current_file_cache(selected_plant, now_ts):
        target_path = build_daily_file_path(selected_plant, now_ts)
        file_df = load_file_for_cache(target_path)

        with shared_data["lock"]:
            pending_rows = shared_data.get("pending_rows_by_file", {}).get(target_path, [])[:]

        if pending_rows:
            pending_df = normalize_measurements_df(pd.DataFrame(pending_rows))
            file_df = normalize_measurements_df(pd.concat([file_df, pending_df], ignore_index=True))

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
        row = {"timestamp": pd.Timestamp(timestamp)}
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
            ts = pd.to_datetime(row.get("timestamp"), errors="coerce")
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
                ts = pd.to_datetime(row.get("timestamp"), errors="coerce")
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

        latest_ts = pd.to_datetime(latest_row.get("timestamp"), errors="coerce")
        if pd.isna(latest_ts):
            return None, False

        if is_null_row(latest_row):
            return pd.Timestamp(latest_ts), True

        null_ts = pd.Timestamp(latest_ts) + measurement_period_delta
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
            return pd.Timestamp(latest_ts), False

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
        recording_file_path = build_daily_file_path(recording_plant, datetime.now())
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
            null_ts = pd.Timestamp(last_real_timestamp) + measurement_period_delta
        else:
            null_ts = pd.Timestamp(datetime.now())

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

    def take_measurement(plant_config):
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

            return {
                "timestamp": pd.Timestamp(datetime.now()),
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
        now_dt = datetime.now()

        with shared_data["lock"]:
            selected_plant = shared_data.get("selected_plant", "local")
            requested_filename = shared_data.get("measurements_filename")
            current_cache_path = shared_data.get("current_file_path")

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

        if current_time - last_measurement_time >= measurement_period_s:
            measurement_row = take_measurement(plant_config)
            if measurement_row is not None:
                last_measurement_time = current_time
                if recording_active:
                    measurement_ts = pd.Timestamp(measurement_row["timestamp"])
                    if awaiting_first_real_sample:
                        leading_null_ts = measurement_ts - measurement_period_delta
                        already_has_boundary = (
                            session_tail_is_null
                            and session_tail_ts is not None
                            and pd.Timestamp(session_tail_ts) == pd.Timestamp(leading_null_ts)
                        )
                        if not already_has_boundary:
                            enqueue_row_for_file(build_null_row(leading_null_ts), recording_plant)
                        awaiting_first_real_sample = False

                    enqueue_row_for_file(measurement_row, recording_plant)
                    last_real_timestamp = measurement_ts
                    session_tail_ts = measurement_ts
                    session_tail_is_null = False

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
