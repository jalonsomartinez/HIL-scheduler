import logging
import time
from datetime import timedelta

import pandas as pd

from istentore_api import AuthenticationError, IstentoreAPI
from schedule_runtime import crop_schedule_frame_to_window, merge_schedule_frames
from shared_state import mutate_locked, snapshot_locked
from time_utils import get_config_tz, now_tz


def _empty_points_by_plant(plant_ids):
    return {plant_id: 0 for plant_id in plant_ids}


def _parse_hhmm_to_minutes(value, key_name):
    text = str(value).strip()
    parts = text.split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid {key_name}='{value}'. Expected HH:MM.")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {key_name}='{value}'. Expected HH:MM.") from exc
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError(f"Invalid {key_name}='{value}'. Expected HH:MM.")
    return (hour * 60) + minute


def _format_window_ts(dt_value):
    return dt_value.strftime("%Y-%m-%d %H:%M:%S %Z")


def _log_fetch_attempt(window_name, target_date, start_dt, end_dt, reason):
    logging.info(
        "Data fetcher: requesting API schedule purpose=%s date=%s reason=%s local_window=[%s -> %s]",
        window_name,
        target_date,
        reason,
        _format_window_ts(start_dt),
        _format_window_ts(end_dt),
    )


def _format_incomplete_fetch_error(window_name, points_by_plant):
    return (
        f"Incomplete {window_name} day-ahead data for all plants "
        f"(LIB={int(points_by_plant.get('lib', 0))} VRFB={int(points_by_plant.get('vrfb', 0))})"
    )


def _update_status(shared_data, **kwargs):
    def _mutate(data):
        if "data_fetcher_status" not in data:
            data["data_fetcher_status"] = {}
        data["data_fetcher_status"].update(kwargs)

    mutate_locked(shared_data, _mutate)


def _reconcile_day_status(shared_data, today_date, tomorrow_date, plant_ids):
    status = snapshot_locked(shared_data, lambda data: data.get("data_fetcher_status", {}).copy())

    previous_today_date = status.get("today_date")
    previous_tomorrow_date = status.get("tomorrow_date")
    previous_tomorrow_fetched = status.get("tomorrow_fetched", False)
    previous_tomorrow_points = status.get("tomorrow_points", 0)
    previous_tomorrow_points_by_plant = status.get("tomorrow_points_by_plant", _empty_points_by_plant(plant_ids))

    updates = {}
    if previous_today_date != today_date:
        can_promote_tomorrow = previous_tomorrow_fetched and previous_tomorrow_date == today_date
        if can_promote_tomorrow:
            updates["today_fetched"] = True
            updates["today_points"] = previous_tomorrow_points
            updates["today_points_by_plant"] = dict(previous_tomorrow_points_by_plant)
            logging.info("Data fetcher: rollover promotion applied for %s", today_date)
        else:
            updates["today_fetched"] = False
            updates["today_points"] = 0
            updates["today_points_by_plant"] = _empty_points_by_plant(plant_ids)

    if previous_tomorrow_date != tomorrow_date:
        updates["tomorrow_fetched"] = False
        updates["tomorrow_points"] = 0
        updates["tomorrow_points_by_plant"] = _empty_points_by_plant(plant_ids)

    if previous_today_date != today_date:
        updates["today_date"] = today_date
    if previous_tomorrow_date != tomorrow_date:
        updates["tomorrow_date"] = tomorrow_date

    if updates:
        _update_status(shared_data, **updates)


def _prune_api_schedule_frames_to_window(shared_data, plant_ids, tz, window_start, window_end):
    existing_map = snapshot_locked(
        shared_data,
        lambda data: {
            plant_id: data.get("api_schedule_df_by_plant", {}).get(plant_id, pd.DataFrame()).copy()
            for plant_id in plant_ids
        },
    )
    pruned_map = {
        plant_id: crop_schedule_frame_to_window(existing_map.get(plant_id), tz, window_start, window_end)
        for plant_id in plant_ids
    }

    def _write_pruned(data):
        schedule_map = data.setdefault("api_schedule_df_by_plant", {})
        for plant_id in plant_ids:
            schedule_map[plant_id] = pruned_map[plant_id]

    mutate_locked(shared_data, _write_pruned)


def _extract_points_by_plant(schedule_df_by_plant, plant_ids):
    points = {}
    for plant_id in plant_ids:
        df = schedule_df_by_plant.get(plant_id)
        points[plant_id] = int(len(df)) if df is not None else 0
    return points


def data_fetcher_agent(config, shared_data):
    """Fetch API schedules for both logical plants and publish to shared state."""
    logging.info("Data fetcher agent started.")

    plant_ids = tuple(config.get("PLANT_IDS", ("lib", "vrfb")))
    tomorrow_poll_start_time = config.get("ISTENTORE_TOMORROW_POLL_START_TIME", "17:30")
    tomorrow_poll_start_minutes = _parse_hhmm_to_minutes(
        tomorrow_poll_start_time,
        "ISTENTORE_TOMORROW_POLL_START_TIME",
    )
    poll_interval_s = float(config.get("DATA_FETCHER_PERIOD_S", 120))
    error_backoff_s = 30
    tz = get_config_tz(config)

    api = None
    password_checked = False
    last_tomorrow_gate_log = {"date": None, "state": None}

    logging.info(
        "Data fetcher config: poll_interval=%ss error_backoff=%ss tomorrow_poll_start_time=%s",
        poll_interval_s,
        error_backoff_s,
        tomorrow_poll_start_time,
    )

    while not shared_data["shutdown_event"].is_set():
        try:
            api_gate = snapshot_locked(
                shared_data,
                lambda data: {
                    "password": data.get("api_password"),
                    "api_connection_runtime": dict(data.get("api_connection_runtime", {}) or {}),
                },
            )
            password = api_gate.get("password")
            api_runtime = dict(api_gate.get("api_connection_runtime", {}) or {})
            api_runtime_state = str(api_runtime.get("state") or "")
            api_allowed = api_runtime_state in {"connected", "error"} or ("state" not in api_runtime)

            if not api_allowed:
                if password_checked:
                    password_checked = False
                    api = None
                    _update_status(shared_data, connected=False, error=None)
                    logging.info("Data fetcher: API connection disabled by runtime state (%s).", api_runtime_state or "unknown")
                time.sleep(error_backoff_s)
                continue

            if not password:
                if password_checked:
                    password_checked = False
                    api = None
                    _update_status(shared_data, connected=False, error=None)
                    logging.info("Data fetcher: API password cleared.")
                time.sleep(error_backoff_s)
                continue

            password_checked = True
            if api is None:
                api = IstentoreAPI(
                    base_url=config.get("ISTENTORE_BASE_URL"),
                    email=config.get("ISTENTORE_EMAIL"),
                    timezone_name=config.get("TIMEZONE_NAME"),
                )
                api.set_password(password)
                logging.info("Data fetcher: API client initialized.")
            elif api._password != password:
                api.set_password(password)
                logging.info("Data fetcher: API password updated.")

            now = now_tz(config)
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            today_end = today_start + timedelta(days=1) - timedelta(minutes=15)
            tomorrow_start = today_start + timedelta(days=1)
            tomorrow_end = tomorrow_start + timedelta(days=1) - timedelta(minutes=15)
            retention_window_end = today_start + timedelta(days=2)
            today_date = today_start.date().isoformat()
            tomorrow_date = tomorrow_start.date().isoformat()

            _reconcile_day_status(shared_data, today_date, tomorrow_date, plant_ids)
            _prune_api_schedule_frames_to_window(shared_data, plant_ids, tz, today_start, retention_window_end)

            status = snapshot_locked(shared_data, lambda data: data.get("data_fetcher_status", {}).copy())
            today_fetched = bool(status.get("today_fetched", False))
            tomorrow_fetched = bool(status.get("tomorrow_fetched", False))

            if not today_fetched:
                try:
                    _log_fetch_attempt(
                        "today",
                        today_date,
                        today_start,
                        today_end,
                        "today missing/incomplete",
                    )
                    schedules = api.get_day_ahead_schedules(today_start, today_end)
                    dfs = {
                        plant_id: api.schedule_to_dataframe(schedules.get(plant_id, {}))
                        for plant_id in plant_ids
                    }
                    existing_map = snapshot_locked(
                        shared_data,
                        lambda data: {
                            plant_id: data.get("api_schedule_df_by_plant", {}).get(plant_id, pd.DataFrame())
                            for plant_id in plant_ids
                        },
                    )
                    merged = {
                        plant_id: crop_schedule_frame_to_window(
                            merge_schedule_frames(existing_map[plant_id], dfs[plant_id]),
                            tz,
                            today_start,
                            retention_window_end,
                        )
                        for plant_id in plant_ids
                    }

                    points_by_plant = _extract_points_by_plant(dfs, plant_ids)
                    total_points = sum(points_by_plant.values())
                    fetched_ok = all(points_by_plant[plant_id] > 0 for plant_id in plant_ids)
                    incomplete_error = _format_incomplete_fetch_error("today", points_by_plant)

                    def _write_today(data):
                        schedule_map = data.get("api_schedule_df_by_plant", {})
                        for plant_id in plant_ids:
                            schedule_map[plant_id] = merged[plant_id]

                    mutate_locked(shared_data, _write_today)
                    _prune_api_schedule_frames_to_window(shared_data, plant_ids, tz, today_start, retention_window_end)

                    _update_status(
                        shared_data,
                        connected=True,
                        today_fetched=fetched_ok,
                        today_date=today_date,
                        today_points=total_points,
                        today_points_by_plant=points_by_plant,
                        error=None if fetched_ok else incomplete_error,
                    )
                    if fetched_ok:
                        logging.info(
                            "Data fetcher: today schedules fetched complete (%s) LIB=%s VRFB=%s",
                            today_date,
                            points_by_plant.get("lib", 0),
                            points_by_plant.get("vrfb", 0),
                        )
                    else:
                        logging.warning(
                            "Data fetcher: today schedules fetched partial (%s) LIB=%s VRFB=%s; will retry",
                            today_date,
                            points_by_plant.get("lib", 0),
                            points_by_plant.get("vrfb", 0),
                        )
                except AuthenticationError as exc:
                    _update_status(shared_data, connected=False, error=f"Authentication failed: {exc}")
                    api = None
                    time.sleep(error_backoff_s)
                    continue
                except Exception as exc:
                    _update_status(shared_data, error=str(exc))
                    logging.error("Data fetcher: error fetching today's schedules: %s", exc)

            now_minutes = (int(now.hour) * 60) + int(now.minute)
            tomorrow_gate_open = now_minutes >= tomorrow_poll_start_minutes
            if last_tomorrow_gate_log["date"] != tomorrow_date:
                last_tomorrow_gate_log = {"date": tomorrow_date, "state": None}

            if not tomorrow_fetched:
                gate_state = "eligible" if tomorrow_gate_open else "waiting"
                if last_tomorrow_gate_log["state"] != gate_state:
                    if tomorrow_gate_open:
                        logging.info(
                            "Data fetcher: tomorrow poll gate eligible date=%s now=%s start=%s",
                            tomorrow_date,
                            now.strftime("%H:%M"),
                            tomorrow_poll_start_time,
                        )
                    else:
                        logging.info(
                            "Data fetcher: tomorrow poll gate waiting date=%s now=%s start=%s",
                            tomorrow_date,
                            now.strftime("%H:%M"),
                            tomorrow_poll_start_time,
                        )
                    last_tomorrow_gate_log["state"] = gate_state

            if not tomorrow_fetched and tomorrow_gate_open:
                try:
                    _log_fetch_attempt(
                        "tomorrow",
                        tomorrow_date,
                        tomorrow_start,
                        tomorrow_end,
                        "tomorrow missing/incomplete + gate open",
                    )
                    schedules = api.get_day_ahead_schedules(tomorrow_start, tomorrow_end)
                    new_dfs = {
                        plant_id: api.schedule_to_dataframe(schedules.get(plant_id, {}))
                        for plant_id in plant_ids
                    }

                    existing_map = snapshot_locked(
                        shared_data,
                        lambda data: {
                            plant_id: data.get("api_schedule_df_by_plant", {}).get(plant_id, pd.DataFrame())
                            for plant_id in plant_ids
                        },
                    )

                    merged = {
                        plant_id: crop_schedule_frame_to_window(
                            merge_schedule_frames(existing_map[plant_id], new_dfs[plant_id]),
                            tz,
                            today_start,
                            retention_window_end,
                        )
                        for plant_id in plant_ids
                    }

                    def _write_tomorrow(data):
                        schedule_map = data.get("api_schedule_df_by_plant", {})
                        for plant_id in plant_ids:
                            schedule_map[plant_id] = merged[plant_id]

                    mutate_locked(shared_data, _write_tomorrow)
                    _prune_api_schedule_frames_to_window(shared_data, plant_ids, tz, today_start, retention_window_end)

                    points_by_plant = _extract_points_by_plant(new_dfs, plant_ids)
                    total_points = sum(points_by_plant.values())
                    fetched_ok = all(points_by_plant[plant_id] > 0 for plant_id in plant_ids)
                    incomplete_error = _format_incomplete_fetch_error("tomorrow", points_by_plant)

                    _update_status(
                        shared_data,
                        connected=True,
                        tomorrow_fetched=fetched_ok,
                        tomorrow_date=tomorrow_date,
                        tomorrow_points=total_points,
                        tomorrow_points_by_plant=points_by_plant,
                        error=None if fetched_ok else incomplete_error,
                    )
                    if fetched_ok:
                        logging.info(
                            "Data fetcher: tomorrow schedules fetched complete (%s) LIB=%s VRFB=%s",
                            tomorrow_date,
                            points_by_plant.get("lib", 0),
                            points_by_plant.get("vrfb", 0),
                        )
                    else:
                        logging.warning(
                            "Data fetcher: tomorrow schedules fetched partial (%s) LIB=%s VRFB=%s; will retry",
                            tomorrow_date,
                            points_by_plant.get("lib", 0),
                            points_by_plant.get("vrfb", 0),
                        )
                except AuthenticationError as exc:
                    _update_status(shared_data, connected=False, error=f"Authentication failed: {exc}")
                    api = None
                    time.sleep(error_backoff_s)
                    continue
                except Exception as exc:
                    _update_status(shared_data, error=str(exc))
                    logging.error("Data fetcher: error fetching tomorrow schedules: %s", exc)

            _update_status(shared_data, last_attempt=now.isoformat())
            time.sleep(poll_interval_s)

        except Exception as exc:
            logging.error("Data fetcher: unexpected error: %s", exc)
            time.sleep(error_backoff_s)

    logging.info("Data fetcher agent stopped.")
