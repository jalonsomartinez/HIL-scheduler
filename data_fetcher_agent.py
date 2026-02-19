import logging
import time
from datetime import timedelta

import pandas as pd

from istentore_api import AuthenticationError, IstentoreAPI
from time_utils import now_tz


def _empty_points_by_plant(plant_ids):
    return {plant_id: 0 for plant_id in plant_ids}


def _update_status(shared_data, **kwargs):
    with shared_data["lock"]:
        if "data_fetcher_status" not in shared_data:
            shared_data["data_fetcher_status"] = {}
        shared_data["data_fetcher_status"].update(kwargs)


def _reconcile_day_status(shared_data, today_date, tomorrow_date, plant_ids):
    with shared_data["lock"]:
        status = shared_data.get("data_fetcher_status", {}).copy()

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


def _merge_schedule(existing_df, new_df):
    if existing_df is None or existing_df.empty:
        return new_df
    if new_df is None or new_df.empty:
        return existing_df

    non_overlapping = existing_df.index.difference(new_df.index)
    return pd.concat([existing_df.loc[non_overlapping], new_df]).sort_index()


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
    poll_start_time = config.get("ISTENTORE_POLL_START_TIME", "17:30")
    poll_interval_s = float(config.get("DATA_FETCHER_PERIOD_S", 120))
    error_backoff_s = 30

    api = None
    password_checked = False

    logging.info(
        "Data fetcher config: poll_interval=%ss error_backoff=%ss poll_start_time=%s",
        poll_interval_s,
        error_backoff_s,
        poll_start_time,
    )

    while not shared_data["shutdown_event"].is_set():
        try:
            with shared_data["lock"]:
                password = shared_data.get("api_password")

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
            today_date = today_start.date().isoformat()
            tomorrow_date = tomorrow_start.date().isoformat()

            _reconcile_day_status(shared_data, today_date, tomorrow_date, plant_ids)

            with shared_data["lock"]:
                status = shared_data.get("data_fetcher_status", {}).copy()
            today_fetched = bool(status.get("today_fetched", False))
            tomorrow_fetched = bool(status.get("tomorrow_fetched", False))

            if not today_fetched:
                try:
                    schedules = api.get_day_ahead_schedules(today_start, today_end)
                    dfs = {
                        plant_id: api.schedule_to_dataframe(schedules.get(plant_id, {}))
                        for plant_id in plant_ids
                    }

                    points_by_plant = _extract_points_by_plant(dfs, plant_ids)
                    total_points = sum(points_by_plant.values())
                    fetched_ok = all(points_by_plant[plant_id] > 0 for plant_id in plant_ids)

                    with shared_data["lock"]:
                        for plant_id in plant_ids:
                            shared_data["api_schedule_df_by_plant"][plant_id] = dfs[plant_id]

                    _update_status(
                        shared_data,
                        connected=True,
                        today_fetched=fetched_ok,
                        today_date=today_date,
                        today_points=total_points,
                        today_points_by_plant=points_by_plant,
                        error=None if fetched_ok else "No complete day-ahead data for all plants",
                    )
                    logging.info(
                        "Data fetcher: today's schedules fetched (%s) LIB=%s VRFB=%s",
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

            current_time = now.strftime("%H:%M")
            if not tomorrow_fetched and current_time >= poll_start_time:
                try:
                    schedules = api.get_day_ahead_schedules(tomorrow_start, tomorrow_end)
                    new_dfs = {
                        plant_id: api.schedule_to_dataframe(schedules.get(plant_id, {}))
                        for plant_id in plant_ids
                    }

                    with shared_data["lock"]:
                        existing_map = {
                            plant_id: shared_data.get("api_schedule_df_by_plant", {}).get(plant_id, pd.DataFrame())
                            for plant_id in plant_ids
                        }

                    merged = {
                        plant_id: _merge_schedule(existing_map[plant_id], new_dfs[plant_id])
                        for plant_id in plant_ids
                    }

                    with shared_data["lock"]:
                        for plant_id in plant_ids:
                            shared_data["api_schedule_df_by_plant"][plant_id] = merged[plant_id]

                    points_by_plant = _extract_points_by_plant(new_dfs, plant_ids)
                    total_points = sum(points_by_plant.values())
                    fetched_ok = all(points_by_plant[plant_id] > 0 for plant_id in plant_ids)

                    _update_status(
                        shared_data,
                        connected=True,
                        tomorrow_fetched=fetched_ok,
                        tomorrow_date=tomorrow_date,
                        tomorrow_points=total_points,
                        tomorrow_points_by_plant=points_by_plant,
                        error=None,
                    )
                    logging.info(
                        "Data fetcher: tomorrow's schedules fetched (%s) LIB=%s VRFB=%s",
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
