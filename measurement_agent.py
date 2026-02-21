import glob
import logging
import math
import os
import time
from collections import deque
from datetime import timedelta

import pandas as pd

from istentore_api import AuthenticationError, IstentoreAPI, IstentoreAPIError
from measurement_posting import build_post_items
from measurement_sampling import (
    ensure_client as sampling_ensure_client,
    get_transport_endpoint as sampling_get_transport_endpoint,
    take_measurement as sampling_take_measurement,
)
from measurement_storage import (
    MEASUREMENT_COLUMNS,
    MEASUREMENT_VALUE_COLUMNS,
    append_rows_to_csv,
    build_daily_file_path as storage_build_daily_file_path,
    build_null_row,
    is_real_row,
    is_null_row,
    load_file_for_cache,
    normalize_measurements_df,
    rows_are_similar,
)
from runtime_contracts import sanitize_plant_name
from shared_state import snapshot_locked
from time_utils import get_config_tz, normalize_datetime_series, normalize_timestamp_value, now_tz, serialize_iso_with_tz


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


def measurement_agent(config, shared_data):
    """Measurement, recording, cache, and API posting for LIB/VRFB."""
    logging.info("Measurement agent started.")

    plant_ids = tuple(config.get("PLANT_IDS", ("lib", "vrfb")))
    plants_cfg = config.get("PLANTS", {})
    tz = get_config_tz(config)

    measurement_period_s = float(config.get("MEASUREMENT_PERIOD_S", 1))
    write_period_s = float(config.get("MEASUREMENTS_WRITE_PERIOD_S", 60))
    measurement_period_delta = timedelta(seconds=measurement_period_s)
    compression_enabled_raw = config.get("MEASUREMENT_COMPRESSION_ENABLED", True)
    configured_tolerances = config.get("MEASUREMENT_COMPRESSION_TOLERANCES", {})

    def parse_bool(value, default):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ["1", "true", "yes", "on"]
        if value is None:
            return default
        return bool(value)

    compression_enabled = parse_bool(compression_enabled_raw, True)
    if not isinstance(configured_tolerances, dict):
        configured_tolerances = {}

    compression_tolerances = {}
    for column in MEASUREMENT_VALUE_COLUMNS:
        default_value = DEFAULT_COMPRESSION_TOLERANCES.get(column, 0.0)
        raw_value = configured_tolerances.get(column, default_value)
        try:
            parsed = float(raw_value)
            compression_tolerances[column] = parsed if parsed >= 0.0 else default_value
        except (TypeError, ValueError):
            compression_tolerances[column] = default_value

    config_post_measurements_enabled = parse_bool(
        config.get("ISTENTORE_POST_MEASUREMENTS_IN_API_MODE", True),
        True,
    )
    measurement_post_period_s = float(config.get("ISTENTORE_MEASUREMENT_POST_PERIOD_S", 60))
    post_queue_maxlen = int(config.get("ISTENTORE_MEASUREMENT_POST_QUEUE_MAXLEN", 2000))
    post_retry_initial_s = float(config.get("ISTENTORE_MEASUREMENT_POST_RETRY_INITIAL_S", 2))
    post_retry_max_s = float(config.get("ISTENTORE_MEASUREMENT_POST_RETRY_MAX_S", 60))

    startup_wall_ts = normalize_timestamp_value(pd.Timestamp(now_tz(config)), tz)
    measurement_anchor_wall = startup_wall_ts.ceil("s")
    measurement_anchor_mono = time.monotonic() + max(
        0.0,
        (measurement_anchor_wall - startup_wall_ts).total_seconds(),
    )
    last_executed_trigger_step = -1

    post_anchor_mono = measurement_anchor_mono
    last_executed_post_step = -1

    api_poster = None
    posting_password_cached = None
    posting_mode_active = False
    api_post_queue = deque()

    last_write_time = time.time()
    last_real_row_by_file = {}
    run_active_by_file = {}

    plant_states = {}
    for plant_id in plant_ids:
        plant_states[plant_id] = {
            "client": None,
            "endpoint_key": None,
            "recording_active": False,
            "recording_file_path": None,
            "awaiting_first_real_sample": False,
            "last_real_timestamp": None,
            "session_tail_ts": None,
            "session_tail_is_null": False,
            "latest_measurement": None,
            "cache_context": None,
        }

    def empty_post_status():
        return {
            "posting_enabled": False,
            "last_success": None,
            "last_attempt": None,
            "last_error": None,
            "pending_queue_count": 0,
            "oldest_pending_age_s": None,
            "last_enqueue": None,
        }

    def ensure_post_status_locked():
        status_map = shared_data.get("measurement_post_status")
        if not isinstance(status_map, dict):
            status_map = {}
        for plant_id in plant_ids:
            existing = status_map.get(plant_id)
            merged = empty_post_status()
            if isinstance(existing, dict):
                merged.update(existing)
            status_map[plant_id] = merged
        shared_data["measurement_post_status"] = status_map
        return status_map

    def now_iso_local():
        return serialize_iso_with_tz(now_tz(config), tz=tz)

    def update_post_status(plant_id, **fields):
        if plant_id not in plant_ids:
            return
        with shared_data["lock"]:
            status_map = ensure_post_status_locked()
            status_map[plant_id].update(fields)

    def set_posting_enabled(enabled):
        with shared_data["lock"]:
            status_map = ensure_post_status_locked()
            for plant_id in plant_ids:
                status_map[plant_id]["posting_enabled"] = bool(enabled)

    def refresh_post_queue_status():
        now_mono = time.monotonic()
        queue_count_by_plant = {plant_id: 0 for plant_id in plant_ids}
        oldest_age_by_plant = {plant_id: None for plant_id in plant_ids}

        for item in api_post_queue:
            plant_id = item.get("plant_id")
            if plant_id not in queue_count_by_plant:
                continue

            queue_count_by_plant[plant_id] += 1
            enqueued_mono = item.get("enqueued_mono")
            if isinstance(enqueued_mono, (int, float)):
                age_s = max(0.0, now_mono - float(enqueued_mono))
                current_oldest = oldest_age_by_plant[plant_id]
                if current_oldest is None or age_s > current_oldest:
                    oldest_age_by_plant[plant_id] = age_s

        with shared_data["lock"]:
            status_map = ensure_post_status_locked()
            for plant_id in plant_ids:
                status_map[plant_id]["pending_queue_count"] = int(queue_count_by_plant[plant_id])
                oldest_age_s = oldest_age_by_plant[plant_id]
                status_map[plant_id]["oldest_pending_age_s"] = None if oldest_age_s is None else round(float(oldest_age_s), 1)

    with shared_data["lock"]:
        ensure_post_status_locked()

    def get_plant_name(plant_id):
        plant_cfg = plants_cfg.get(plant_id, {})
        return plant_cfg.get("name", plant_id.upper()), plant_id

    def build_daily_file_path(plant_id, timestamp):
        plant_name, fallback = get_plant_name(plant_id)
        return storage_build_daily_file_path(plant_name, fallback, timestamp, tz, now_tz(config))

    def refresh_aggregate_measurements_df():
        with shared_data["lock"]:
            dfs = []
            for plant_id in plant_ids:
                df = shared_data.get("current_file_df_by_plant", {}).get(plant_id, pd.DataFrame())
                if df is not None and not df.empty:
                    tagged = df.copy()
                    tagged["plant_id"] = plant_id
                    dfs.append(tagged)
            if dfs:
                shared_data["measurements_df"] = pd.concat(dfs, ignore_index=True)
            else:
                shared_data["measurements_df"] = pd.DataFrame()

    def refresh_current_file_cache(plant_id, now_ts):
        file_path = build_daily_file_path(plant_id, now_ts)
        file_df = load_file_for_cache(file_path, tz)

        with shared_data["lock"]:
            pending_rows = shared_data.get("pending_rows_by_file", {}).get(file_path, [])[:]

        if pending_rows:
            pending_df = normalize_measurements_df(pd.DataFrame(pending_rows), tz)
            file_df = normalize_measurements_df(pd.concat([file_df, pending_df], ignore_index=True), tz)

        with shared_data["lock"]:
            shared_data["current_file_path_by_plant"][plant_id] = file_path
            shared_data["current_file_df_by_plant"][plant_id] = file_df

        refresh_aggregate_measurements_df()
        return file_path

    def upsert_row_to_current_cache(plant_id, file_path, row, replace_last=False):
        row_df = pd.DataFrame([row], columns=MEASUREMENT_COLUMNS)
        row_df["timestamp"] = normalize_datetime_series(row_df["timestamp"], tz)

        with shared_data["lock"]:
            current_path = shared_data.get("current_file_path_by_plant", {}).get(plant_id)
            if current_path != file_path:
                return
            current_df = shared_data.get("current_file_df_by_plant", {}).get(plant_id, pd.DataFrame())
            if current_df is None or current_df.empty:
                updated = row_df
            elif replace_last:
                updated = current_df.copy()
                updated.iloc[-1] = row_df.iloc[0]
            else:
                updated = pd.concat([current_df, row_df], ignore_index=True)
            shared_data["current_file_df_by_plant"][plant_id] = updated

        refresh_aggregate_measurements_df()

    def enqueue_row_for_file(row, plant_id):
        file_path = build_daily_file_path(plant_id, row["timestamp"])
        append_new = False
        replace_previous = False

        row_is_real = is_real_row(row)
        prev_real_row = last_real_row_by_file.get(file_path)
        run_active = bool(run_active_by_file.get(file_path, False))

        with shared_data["lock"]:
            pending = shared_data.setdefault("pending_rows_by_file", {})
            rows = pending.setdefault(file_path, [])

            if not compression_enabled:
                rows.append(row)
                append_new = True
            elif not row_is_real:
                rows.append(row)
                append_new = True
                last_real_row_by_file[file_path] = None
                run_active_by_file[file_path] = False
            elif prev_real_row is None or not rows_are_similar(prev_real_row, row, compression_tolerances):
                rows.append(row)
                append_new = True
                last_real_row_by_file[file_path] = row
                run_active_by_file[file_path] = False
            elif not run_active:
                rows.append(row)
                append_new = True
                last_real_row_by_file[file_path] = row
                run_active_by_file[file_path] = True
            else:
                if rows:
                    rows[-1] = row
                    replace_previous = True
                else:
                    rows.append(row)
                    append_new = True
                last_real_row_by_file[file_path] = row
                run_active_by_file[file_path] = True

        if append_new or replace_previous:
            upsert_row_to_current_cache(plant_id, file_path, row, replace_last=replace_previous)
        return file_path

    def flush_pending_rows(force=False):
        nonlocal last_write_time

        active_recording_paths = set()
        if compression_enabled and not force:
            for plant_id in plant_ids:
                state = plant_states[plant_id]
                path = state.get("recording_file_path")
                if state.get("recording_active") and path:
                    active_recording_paths.add(path)

        with shared_data["lock"]:
            pending = shared_data.get("pending_rows_by_file", {})
            if not pending:
                return

            pending_snapshot = {}
            retained_rows = {}
            for path, rows in pending.items():
                if not rows:
                    continue
                keep_tail = compression_enabled and (not force) and path in active_recording_paths
                if keep_tail:
                    retained_rows[path] = [rows[-1]]
                    if len(rows) > 1:
                        pending_snapshot[path] = rows[:-1]
                else:
                    pending_snapshot[path] = rows[:]

            shared_data["pending_rows_by_file"] = retained_rows

        if not pending_snapshot:
            last_write_time = time.time()
            return

        failed = {}
        for path, rows in pending_snapshot.items():
            try:
                append_rows_to_csv(path, rows, tz)
            except Exception as exc:
                logging.error("Measurement: failed writing %s: %s", path, exc)
                failed[path] = rows

        if failed:
            with shared_data["lock"]:
                pending = shared_data.setdefault("pending_rows_by_file", {})
                for path, rows in failed.items():
                    pending[path] = rows + pending.get(path, [])

        last_write_time = time.time()

    def find_latest_row_for_plant(plant_id):
        plant_name, fallback = get_plant_name(plant_id)
        safe_name = sanitize_plant_name(plant_name, fallback)
        pattern = os.path.join("data", f"*_{safe_name}.csv")
        paths = sorted(glob.glob(pattern))

        latest_ts = None
        latest_path = None
        latest_row = None

        for path in paths:
            df = load_file_for_cache(path, tz)
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

    def sanitize_historical_tail(plant_id):
        latest_path, latest_row = find_latest_row_for_plant(plant_id)
        if latest_path is None or latest_row is None:
            return None, False

        latest_ts = normalize_timestamp_value(latest_row.get("timestamp"), tz)
        if pd.isna(latest_ts):
            return None, False

        if is_null_row(latest_row):
            return latest_ts, True

        null_ts = latest_ts + measurement_period_delta
        null_row = build_null_row(null_ts, tz)
        append_rows_to_csv(latest_path, [null_row], tz)
        upsert_row_to_current_cache(plant_id, latest_path, null_row)
        return null_ts, True

    def start_recording_session(plant_id, requested_path):
        state = plant_states[plant_id]
        state["recording_active"] = True
        state["recording_file_path"] = requested_path or build_daily_file_path(plant_id, now_tz(config))
        state["awaiting_first_real_sample"] = True
        state["last_real_timestamp"] = None
        state["session_tail_ts"], state["session_tail_is_null"] = sanitize_historical_tail(plant_id)

        with shared_data["lock"]:
            shared_data["measurements_filename_by_plant"][plant_id] = state["recording_file_path"]

        logging.info("Measurement: recording started for %s -> %s", plant_id.upper(), state["recording_file_path"])

    def stop_recording_session(plant_id, clear_shared_flag=True):
        state = plant_states[plant_id]
        if not state["recording_active"]:
            if clear_shared_flag:
                with shared_data["lock"]:
                    shared_data["measurements_filename_by_plant"][plant_id] = None
            return

        if state["last_real_timestamp"] is not None:
            null_ts = normalize_timestamp_value(state["last_real_timestamp"], tz) + measurement_period_delta
        else:
            null_ts = pd.Timestamp(now_tz(config))

        enqueue_row_for_file(build_null_row(null_ts, tz), plant_id)
        flush_pending_rows(force=True)

        if clear_shared_flag:
            with shared_data["lock"]:
                shared_data["measurements_filename_by_plant"][plant_id] = None

        stopped_file_path = state["recording_file_path"]

        state["recording_active"] = False
        state["recording_file_path"] = None
        state["awaiting_first_real_sample"] = False
        state["last_real_timestamp"] = None
        state["session_tail_ts"] = None
        state["session_tail_is_null"] = False
        if stopped_file_path is not None:
            last_real_row_by_file[stopped_file_path] = None
            run_active_by_file[stopped_file_path] = False

        logging.info("Measurement: recording stopped for %s", plant_id.upper())

    def ensure_client(plant_id, transport_mode):
        state = plant_states[plant_id]
        endpoint = sampling_get_transport_endpoint(config, plant_id, transport_mode)
        client = sampling_ensure_client(state, endpoint, plant_id, transport_mode)
        return client, endpoint

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

    def enqueue_post_item(plant_id, metric, series_id, value, timestamp_iso_utc):
        if series_id is None or value is None:
            return
        if len(api_post_queue) >= post_queue_maxlen:
            api_post_queue.popleft()
            logging.warning("Measurement: API queue full, dropping oldest payload")
        api_post_queue.append(
            {
                "plant_id": plant_id,
                "metric": str(metric),
                "series_id": int(series_id),
                "value": float(value),
                "timestamp": timestamp_iso_utc,
                "attempt": 0,
                "next_try_mono": time.monotonic(),
                "enqueued_mono": time.monotonic(),
            }
        )
        update_post_status(plant_id, last_enqueue=now_iso_local())

    def enqueue_latest_measurement_posts():
        for plant_id in plant_ids:
            row = plant_states[plant_id]["latest_measurement"]
            if row is None:
                continue

            model = (plants_cfg.get(plant_id, {}) or {}).get("model", {})
            series = (plants_cfg.get(plant_id, {}) or {}).get("measurement_series", {})
            for metric, series_id, value, timestamp_iso in build_post_items(row, model, series, tz):
                enqueue_post_item(plant_id, metric, series_id, value, timestamp_iso)

    def drain_api_post_queue(password, max_posts_per_loop=12):
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
            plant_id = item.get("plant_id")
            metric = item.get("metric")
            attempt_no = int(item.get("attempt", 0)) + 1
            measurement_timestamp = item.get("timestamp")
            attempt_ts = now_iso_local()
            update_post_status(
                plant_id,
                last_attempt={
                    "timestamp": attempt_ts,
                    "metric": metric,
                    "value": item.get("value"),
                    "series_id": item.get("series_id"),
                    "measurement_timestamp": measurement_timestamp,
                    "attempt": attempt_no,
                    "result": "attempting",
                    "error": None,
                    "next_retry_seconds": None,
                },
            )
            try:
                poster.post_measurement(item["series_id"], item["value"], timestamp=item["timestamp"])
                sent += 1
                success_ts = now_iso_local()
                update_post_status(
                    plant_id,
                    last_success={
                        "timestamp": success_ts,
                        "metric": metric,
                        "value": item.get("value"),
                        "series_id": item.get("series_id"),
                        "measurement_timestamp": measurement_timestamp,
                    },
                    last_attempt={
                        "timestamp": success_ts,
                        "metric": metric,
                        "value": item.get("value"),
                        "series_id": item.get("series_id"),
                        "measurement_timestamp": measurement_timestamp,
                        "attempt": attempt_no,
                        "result": "success",
                        "error": None,
                        "next_retry_seconds": None,
                    },
                    last_error=None,
                )
            except (AuthenticationError, IstentoreAPIError, Exception) as exc:
                item["attempt"] += 1
                delay_s = min(post_retry_initial_s * (2 ** (item["attempt"] - 1)), post_retry_max_s)
                item["next_try_mono"] = time.monotonic() + delay_s
                api_post_queue.append(item)
                error_text = str(exc)
                failure_ts = now_iso_local()
                update_post_status(
                    plant_id,
                    last_attempt={
                        "timestamp": failure_ts,
                        "metric": metric,
                        "value": item.get("value"),
                        "series_id": item.get("series_id"),
                        "measurement_timestamp": measurement_timestamp,
                        "attempt": int(item.get("attempt", 0)),
                        "result": "failed",
                        "error": error_text,
                        "next_retry_seconds": round(float(delay_s), 1),
                    },
                    last_error={"timestamp": failure_ts, "message": error_text},
                )
                logging.warning(
                    "Measurement: API post failed series=%s attempt=%s retry_in=%.1fs error=%s",
                    item["series_id"],
                    item["attempt"],
                    delay_s,
                    exc,
                )
                break

    while not shared_data["shutdown_event"].is_set():
        current_time = time.time()
        now_dt = now_tz(config)

        snapshot = snapshot_locked(
            shared_data,
            lambda data: {
                "transport_mode": data.get("transport_mode", "local"),
                "requested_files": dict(data.get("measurements_filename_by_plant", {})),
                "active_schedule_source": data.get("active_schedule_source", "manual"),
                "api_password": data.get("api_password"),
                "posting_toggle_enabled": bool(data.get("measurement_posting_enabled", config_post_measurements_enabled)),
                "current_paths": dict(data.get("current_file_path_by_plant", {})),
            },
        )
        transport_mode = snapshot["transport_mode"]
        requested_files = snapshot["requested_files"]
        active_schedule_source = snapshot["active_schedule_source"]
        api_password = snapshot["api_password"]
        posting_toggle_enabled = bool(snapshot["posting_toggle_enabled"])
        current_paths = snapshot["current_paths"]

        for plant_id in plant_ids:
            state = plant_states[plant_id]

            expected_cache_path = build_daily_file_path(plant_id, now_dt)
            cache_context = (plant_id, now_dt.strftime("%Y%m%d"))
            if state["cache_context"] != cache_context or current_paths.get(plant_id) != expected_cache_path:
                refresh_current_file_cache(plant_id, now_dt)
                state["cache_context"] = cache_context

            _, endpoint = ensure_client(plant_id, transport_mode)

            requested_filename = requested_files.get(plant_id)
            if not state["recording_active"] and requested_filename is not None:
                start_recording_session(plant_id, requested_filename)
            elif state["recording_active"] and requested_filename is None:
                stop_recording_session(plant_id, clear_shared_flag=False)

            if state["recording_active"]:
                expected_recording_file = build_daily_file_path(plant_id, now_dt)
                if expected_recording_file != state["recording_file_path"]:
                    state["recording_file_path"] = expected_recording_file
                    with shared_data["lock"]:
                        shared_data["measurements_filename_by_plant"][plant_id] = expected_recording_file
                    logging.info("Measurement: midnight rollover for %s -> %s", plant_id.upper(), expected_recording_file)

        current_step = math.floor((time.monotonic() - measurement_anchor_mono) / measurement_period_s)
        if current_step >= 0 and current_step > last_executed_trigger_step:
            last_executed_trigger_step = current_step
            scheduled_step_ts = measurement_anchor_wall + pd.Timedelta(seconds=current_step * measurement_period_s)

            for plant_id in plant_ids:
                state = plant_states[plant_id]
                _, endpoint = ensure_client(plant_id, transport_mode)

                row = sampling_take_measurement(state["client"], endpoint, scheduled_step_ts, tz, plant_id)
                if row is None:
                    continue

                state["latest_measurement"] = row.copy()

                if not state["recording_active"]:
                    continue

                measurement_ts = normalize_timestamp_value(row["timestamp"], tz)
                if state["awaiting_first_real_sample"]:
                    leading_null_ts = measurement_ts - measurement_period_delta
                    already_has_boundary = (
                        state["session_tail_is_null"]
                        and state["session_tail_ts"] is not None
                        and normalize_timestamp_value(state["session_tail_ts"], tz)
                        == normalize_timestamp_value(leading_null_ts, tz)
                    )
                    if not already_has_boundary:
                        enqueue_row_for_file(build_null_row(leading_null_ts, tz), plant_id)
                    state["awaiting_first_real_sample"] = False

                enqueue_row_for_file(row, plant_id)
                state["last_real_timestamp"] = measurement_ts
                state["session_tail_ts"] = measurement_ts
                state["session_tail_is_null"] = False

        posting_mode_now = (
            posting_toggle_enabled and active_schedule_source == "api" and bool(api_password)
        )
        set_posting_enabled(posting_mode_now)

        if not posting_mode_now and posting_mode_active:
            if api_post_queue:
                api_post_queue.clear()
            ensure_api_poster(None)
        posting_mode_active = posting_mode_now

        current_post_step = math.floor((time.monotonic() - post_anchor_mono) / measurement_post_period_s)
        if posting_mode_active and current_post_step >= 0 and current_post_step > last_executed_post_step:
            last_executed_post_step = current_post_step
            enqueue_latest_measurement_posts()

        if posting_mode_active:
            drain_api_post_queue(api_password)

        refresh_post_queue_status()

        if current_time - last_write_time >= write_period_s:
            flush_pending_rows(force=False)

        time.sleep(0.1)

    logging.info("Measurement agent stopping.")
    for plant_id in plant_ids:
        stop_recording_session(plant_id, clear_shared_flag=False)

    flush_pending_rows(force=True)

    for plant_id in plant_ids:
        client = plant_states[plant_id]["client"]
        try:
            if client is not None:
                client.close()
        except Exception:
            pass

    logging.info("Measurement agent stopped.")
