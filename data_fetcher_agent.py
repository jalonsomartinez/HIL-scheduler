import logging
import time
from datetime import timedelta

import pandas as pd

from runtime.api_runtime_state import ensure_api_connection_runtime, publish_api_fetch_health
from istentore_api import AuthenticationError, IstentoreAPI
from scheduling.runtime import crop_schedule_frame_to_window, merge_schedule_frames
from runtime.shared_state import mutate_locked, snapshot_locked
from time_utils import get_config_tz, now_tz

_SCHEDULE_COLUMNS = ["power_setpoint_kw", "reactive_power_setpoint_kvar"]
_DAY_AHEAD_KEY = "api_day_ahead_schedule_df_by_plant"
_MFRR_KEY = "api_mfrr_schedule_df_by_plant"
_TOTAL_KEY = "api_schedule_df_by_plant"


def _empty_points_by_plant(plant_ids):
    return {plant_id: 0 for plant_id in plant_ids}


def _empty_schedule_df():
    return pd.DataFrame(columns=_SCHEDULE_COLUMNS)


def _schedule_map_snapshot(data, key, plant_ids, fallback_map=None):
    raw_map = data.get(key, fallback_map if isinstance(fallback_map, dict) else {})
    if not isinstance(raw_map, dict):
        raw_map = {}
    snapshot = {}
    for plant_id in plant_ids:
        value = raw_map.get(plant_id, _empty_schedule_df())
        snapshot[plant_id] = value.copy() if isinstance(value, pd.DataFrame) else _empty_schedule_df()
    return snapshot


def _snapshot_schedule_maps(shared_data, plant_ids):
    return snapshot_locked(
        shared_data,
        lambda data: {
            _DAY_AHEAD_KEY: _schedule_map_snapshot(
                data,
                _DAY_AHEAD_KEY,
                plant_ids,
                fallback_map=data.get(_TOTAL_KEY, {}),
            ),
            _MFRR_KEY: _schedule_map_snapshot(data, _MFRR_KEY, plant_ids, fallback_map={}),
            _TOTAL_KEY: _schedule_map_snapshot(data, _TOTAL_KEY, plant_ids, fallback_map={}),
        },
    )


def _write_schedule_maps(shared_data, plant_ids, day_ahead_map, mfrr_map, total_map):
    def _mutate(data):
        day_ahead_target = data.setdefault(_DAY_AHEAD_KEY, {})
        mfrr_target = data.setdefault(_MFRR_KEY, {})
        total_target = data.setdefault(_TOTAL_KEY, {})
        for plant_id in plant_ids:
            day_ahead_target[plant_id] = day_ahead_map.get(plant_id, _empty_schedule_df())
            mfrr_target[plant_id] = mfrr_map.get(plant_id, _empty_schedule_df())
            total_target[plant_id] = total_map.get(plant_id, _empty_schedule_df())

    mutate_locked(shared_data, _mutate)


def _zero_schedule_df(index):
    if index is None or len(index) == 0:
        return _empty_schedule_df()
    return pd.DataFrame(
        {
            "power_setpoint_kw": 0.0,
            "reactive_power_setpoint_kvar": 0.0,
        },
        index=index.copy().sort_values(),
    )


def _compose_total_schedule_frame(day_ahead_df, mfrr_df, tz):
    day_ahead_norm = normalize_schedule(day_ahead_df, tz)
    mfrr_norm = normalize_schedule(mfrr_df, tz)

    union_index = pd.DatetimeIndex([])
    if day_ahead_norm is not None and not day_ahead_norm.empty:
        union_index = union_index.union(day_ahead_norm.index)
    if mfrr_norm is not None and not mfrr_norm.empty:
        union_index = union_index.union(mfrr_norm.index)
    union_index = union_index.sort_values()
    if len(union_index) == 0:
        return _empty_schedule_df()

    day_ahead_p = _numeric_series_on_index(day_ahead_norm, "power_setpoint_kw", union_index)
    day_ahead_q = _numeric_series_on_index(day_ahead_norm, "reactive_power_setpoint_kvar", union_index)
    mfrr_p = _numeric_series_on_index(mfrr_norm, "power_setpoint_kw", union_index)

    return pd.DataFrame(
        {
            "power_setpoint_kw": day_ahead_p + mfrr_p,
            "reactive_power_setpoint_kvar": day_ahead_q,
        },
        index=union_index,
    ).sort_index()


def _compose_total_schedule_map(day_ahead_map, mfrr_map, plant_ids, tz, window_start, window_end):
    composed = {}
    for plant_id in plant_ids:
        composed[plant_id] = crop_schedule_frame_to_window(
            _compose_total_schedule_frame(day_ahead_map.get(plant_id), mfrr_map.get(plant_id), tz),
            tz,
            window_start,
            window_end,
        )
    return composed


def normalize_schedule(schedule_df, tz):
    return crop_schedule_frame_to_window(schedule_df, tz, None, None)


def _numeric_series_on_index(df, column_name, index):
    if df is None or df.empty or column_name not in df.columns:
        return pd.Series(0.0, index=index, dtype=float)
    series = pd.to_numeric(df[column_name], errors="coerce")
    return series.reindex(index).fillna(0.0)


def _replace_schedule_maps_and_recompose_total(
    shared_data,
    plant_ids,
    tz,
    window_start,
    window_end,
    *,
    day_ahead_map=None,
    mfrr_map=None,
):
    current_maps = _snapshot_schedule_maps(shared_data, plant_ids)
    day_map = day_ahead_map if day_ahead_map is not None else current_maps.get(_DAY_AHEAD_KEY, {})
    mfrr_source = mfrr_map if mfrr_map is not None else current_maps.get(_MFRR_KEY, {})

    day_pruned = {
        plant_id: crop_schedule_frame_to_window(day_map.get(plant_id), tz, window_start, window_end)
        for plant_id in plant_ids
    }
    mfrr_pruned = {
        plant_id: crop_schedule_frame_to_window(mfrr_source.get(plant_id), tz, window_start, window_end)
        for plant_id in plant_ids
    }
    total_map = _compose_total_schedule_map(day_pruned, mfrr_pruned, plant_ids, tz, window_start, window_end)
    _write_schedule_maps(shared_data, plant_ids, day_pruned, mfrr_pruned, total_map)
    return {
        _DAY_AHEAD_KEY: day_pruned,
        _MFRR_KEY: mfrr_pruned,
        _TOTAL_KEY: total_map,
    }


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


def _log_fetch_attempt(window_name, target_date, start_dt, end_dt, reason, *, level=logging.INFO):
    logging.log(
        level,
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


def _default_mfrr_poll_status(poll_period_s):
    try:
        cadence_s = float(poll_period_s)
    except (TypeError, ValueError):
        cadence_s = 60.0
    if cadence_s <= 0.0:
        cadence_s = 60.0
    return {
        "last_attempt_at": None,
        "last_success_at": None,
        "last_result": "never",
        "last_error": None,
        "last_points_lib": 0,
        "next_scheduled_at": None,
        "poll_period_s": cadence_s,
    }


def _update_mfrr_poll_status(shared_data, *, poll_period_s=None, **kwargs):
    def _mutate(data):
        status = data.setdefault("data_fetcher_status", {})
        current = status.get("mfrr_poll")
        if isinstance(current, dict):
            mfrr_poll = dict(current)
        else:
            mfrr_poll = _default_mfrr_poll_status(poll_period_s)
        if poll_period_s is not None:
            try:
                cadence_s = float(poll_period_s)
            except (TypeError, ValueError):
                cadence_s = 60.0
            if cadence_s <= 0.0:
                cadence_s = 60.0
            mfrr_poll["poll_period_s"] = cadence_s
        mfrr_poll.update(kwargs)
        status["mfrr_poll"] = mfrr_poll

    mutate_locked(shared_data, _mutate)


def _snapshot_mfrr_poll_status(shared_data):
    status = snapshot_locked(shared_data, lambda data: dict(data.get("data_fetcher_status", {}) or {}))
    mfrr_poll = status.get("mfrr_poll")
    if isinstance(mfrr_poll, dict):
        return dict(mfrr_poll)
    return _default_mfrr_poll_status(60)


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _mfrr_poll_transition_level(previous_poll, *, result, lib_points):
    previous = dict(previous_poll or {})
    previous_result = str(previous.get("last_result") or "never")
    previous_points = _safe_int(previous.get("last_points_lib"), default=0)
    if previous_result != str(result) or previous_points != _safe_int(lib_points, default=0):
        return logging.INFO
    return logging.DEBUG


def _log_mfrr_poll_result(previous_poll, *, result, lib_points, vrfb_points):
    level = _mfrr_poll_transition_level(previous_poll, result=result, lib_points=lib_points)
    logging.log(
        level,
        "Data fetcher: mFRR poll result=%s LIB=%s VRFB=%s",
        str(result),
        _safe_int(lib_points, default=0),
        _safe_int(vrfb_points, default=0),
    )


def _compute_next_scheduled_at(config, next_attempt_mono):
    wait_s = max(0.0, float(next_attempt_mono) - float(time.monotonic()))
    return now_tz(config) + timedelta(seconds=wait_s)


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


def _extract_points_by_plant(schedule_df_by_plant, plant_ids):
    points = {}
    for plant_id in plant_ids:
        df = schedule_df_by_plant.get(plant_id)
        points[plant_id] = int(len(df)) if df is not None else 0
    return points


def data_fetcher_agent(config, shared_data):
    """Fetch API schedules and publish day-ahead, mFRR, and total schedules."""
    logging.info("Data fetcher agent started.")
    ensure_api_connection_runtime(shared_data)

    plant_ids = tuple(config.get("PLANT_IDS", ("lib", "vrfb")))
    tomorrow_poll_start_time = config.get("ISTENTORE_TOMORROW_POLL_START_TIME", "17:30")
    tomorrow_poll_start_minutes = _parse_hhmm_to_minutes(
        tomorrow_poll_start_time,
        "ISTENTORE_TOMORROW_POLL_START_TIME",
    )
    poll_interval_s = float(config.get("DATA_FETCHER_PERIOD_S", 120))
    mfrr_poll_period_s = float(config.get("ISTENTORE_MFRR_POLL_PERIOD_S", 60))
    loop_sleep_s = max(0.1, min(poll_interval_s, mfrr_poll_period_s))
    error_backoff_s = 30
    tz = get_config_tz(config)

    api = None
    password_checked = False
    last_tomorrow_gate_log = {"date": None, "state": None}
    next_day_ahead_attempt_mono = 0.0
    next_mfrr_attempt_mono = 0.0
    _update_mfrr_poll_status(shared_data, poll_period_s=mfrr_poll_period_s)

    logging.info(
        (
            "Data fetcher config: poll_interval=%ss mfrr_poll_period=%ss "
            "error_backoff=%ss tomorrow_poll_start_time=%s"
        ),
        poll_interval_s,
        mfrr_poll_period_s,
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
                previous_mfrr_poll = _snapshot_mfrr_poll_status(shared_data)
                _update_mfrr_poll_status(
                    shared_data,
                    poll_period_s=mfrr_poll_period_s,
                    last_result="disabled",
                    last_error=None,
                    last_points_lib=0,
                    next_scheduled_at=None,
                )
                _log_mfrr_poll_result(previous_mfrr_poll, result="disabled", lib_points=0, vrfb_points=0)
                publish_api_fetch_health(shared_data, state="disabled", now_value=now_tz(config))
                if password_checked:
                    password_checked = False
                    api = None
                    _update_status(shared_data, connected=False, error=None)
                    logging.info("Data fetcher: API connection disabled by runtime state (%s).", api_runtime_state or "unknown")
                time.sleep(error_backoff_s)
                continue

            if not password:
                previous_mfrr_poll = _snapshot_mfrr_poll_status(shared_data)
                _update_mfrr_poll_status(
                    shared_data,
                    poll_period_s=mfrr_poll_period_s,
                    last_result="disabled",
                    last_error=None,
                    last_points_lib=0,
                    next_scheduled_at=None,
                )
                _log_mfrr_poll_result(previous_mfrr_poll, result="disabled", lib_points=0, vrfb_points=0)
                desired_state = str(api_runtime.get("desired_state") or "disconnected")
                if desired_state == "connected":
                    publish_api_fetch_health(
                        shared_data,
                        state="error",
                        now_value=now_tz(config),
                        error={
                            "timestamp": now_tz(config),
                            "code": "client_init_failed",
                            "message": "missing API password",
                        },
                    )
                else:
                    publish_api_fetch_health(shared_data, state="disabled", now_value=now_tz(config))
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
            now_mono = time.monotonic()
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            today_end = today_start + timedelta(days=1) - timedelta(minutes=15)
            tomorrow_start = today_start + timedelta(days=1)
            tomorrow_end = tomorrow_start + timedelta(days=1) - timedelta(minutes=15)
            retention_window_end = today_start + timedelta(days=2)
            today_date = today_start.date().isoformat()
            tomorrow_date = tomorrow_start.date().isoformat()

            _reconcile_day_status(shared_data, today_date, tomorrow_date, plant_ids)
            _replace_schedule_maps_and_recompose_total(
                shared_data,
                plant_ids,
                tz,
                today_start,
                retention_window_end,
            )

            status = snapshot_locked(shared_data, lambda data: data.get("data_fetcher_status", {}).copy())
            today_fetched = bool(status.get("today_fetched", False))
            tomorrow_fetched = bool(status.get("tomorrow_fetched", False))

            day_ahead_attempted = False
            if now_mono >= next_day_ahead_attempt_mono:
                if not today_fetched:
                    day_ahead_attempted = True
                    try:
                        publish_api_fetch_health(shared_data, now_value=now, last_attempt=now)
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
                        existing_maps = _snapshot_schedule_maps(shared_data, plant_ids)
                        existing_day_ahead = existing_maps.get(_DAY_AHEAD_KEY, {})
                        merged_day_ahead = {
                            plant_id: crop_schedule_frame_to_window(
                                merge_schedule_frames(existing_day_ahead.get(plant_id), dfs[plant_id]),
                                tz,
                                today_start,
                                retention_window_end,
                            )
                            for plant_id in plant_ids
                        }
                        _replace_schedule_maps_and_recompose_total(
                            shared_data,
                            plant_ids,
                            tz,
                            today_start,
                            retention_window_end,
                            day_ahead_map=merged_day_ahead,
                        )

                        points_by_plant = _extract_points_by_plant(dfs, plant_ids)
                        total_points = sum(points_by_plant.values())
                        fetched_ok = all(points_by_plant[plant_id] > 0 for plant_id in plant_ids)
                        incomplete_error = _format_incomplete_fetch_error("today", points_by_plant)

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
                            publish_api_fetch_health(
                                shared_data,
                                state="ok",
                                now_value=now,
                                last_attempt=now,
                                last_success=now,
                            )
                            logging.info(
                                "Data fetcher: today schedules fetched complete (%s) LIB=%s VRFB=%s",
                                today_date,
                                points_by_plant.get("lib", 0),
                                points_by_plant.get("vrfb", 0),
                            )
                        else:
                            publish_api_fetch_health(
                                shared_data,
                                state="error",
                                now_value=now,
                                last_attempt=now,
                                error={
                                    "timestamp": now,
                                    "code": "fetch_failed",
                                    "message": incomplete_error,
                                },
                            )
                            logging.warning(
                                "Data fetcher: today schedules fetched partial (%s) LIB=%s VRFB=%s; will retry",
                                today_date,
                                points_by_plant.get("lib", 0),
                                points_by_plant.get("vrfb", 0),
                            )
                    except AuthenticationError as exc:
                        _update_status(shared_data, connected=False, error=f"Authentication failed: {exc}")
                        publish_api_fetch_health(
                            shared_data,
                            state="error",
                            now_value=now_tz(config),
                            error={
                                "timestamp": now_tz(config),
                                "code": "auth_failed",
                                "message": f"Authentication failed: {exc}",
                            },
                        )
                        api = None
                        time.sleep(error_backoff_s)
                        continue
                    except Exception as exc:
                        _update_status(shared_data, error=str(exc))
                        publish_api_fetch_health(
                            shared_data,
                            state="error",
                            now_value=now_tz(config),
                            error={
                                "timestamp": now_tz(config),
                                "code": "fetch_failed",
                                "message": str(exc),
                            },
                        )
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
                    day_ahead_attempted = True
                    try:
                        publish_api_fetch_health(shared_data, now_value=now, last_attempt=now)
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

                        existing_maps = _snapshot_schedule_maps(shared_data, plant_ids)
                        existing_day_ahead = existing_maps.get(_DAY_AHEAD_KEY, {})
                        merged_day_ahead = {
                            plant_id: crop_schedule_frame_to_window(
                                merge_schedule_frames(existing_day_ahead.get(plant_id), new_dfs[plant_id]),
                                tz,
                                today_start,
                                retention_window_end,
                            )
                            for plant_id in plant_ids
                        }
                        _replace_schedule_maps_and_recompose_total(
                            shared_data,
                            plant_ids,
                            tz,
                            today_start,
                            retention_window_end,
                            day_ahead_map=merged_day_ahead,
                        )

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
                            publish_api_fetch_health(
                                shared_data,
                                state="ok",
                                now_value=now,
                                last_attempt=now,
                                last_success=now,
                            )
                            logging.info(
                                "Data fetcher: tomorrow schedules fetched complete (%s) LIB=%s VRFB=%s",
                                tomorrow_date,
                                points_by_plant.get("lib", 0),
                                points_by_plant.get("vrfb", 0),
                            )
                        else:
                            publish_api_fetch_health(
                                shared_data,
                                state="error",
                                now_value=now,
                                last_attempt=now,
                                error={
                                    "timestamp": now,
                                    "code": "fetch_failed",
                                    "message": incomplete_error,
                                },
                            )
                            logging.warning(
                                "Data fetcher: tomorrow schedules fetched partial (%s) LIB=%s VRFB=%s; will retry",
                                tomorrow_date,
                                points_by_plant.get("lib", 0),
                                points_by_plant.get("vrfb", 0),
                            )
                    except AuthenticationError as exc:
                        _update_status(shared_data, connected=False, error=f"Authentication failed: {exc}")
                        publish_api_fetch_health(
                            shared_data,
                            state="error",
                            now_value=now_tz(config),
                            error={
                                "timestamp": now_tz(config),
                                "code": "auth_failed",
                                "message": f"Authentication failed: {exc}",
                            },
                        )
                        api = None
                        time.sleep(error_backoff_s)
                        continue
                    except Exception as exc:
                        _update_status(shared_data, error=str(exc))
                        publish_api_fetch_health(
                            shared_data,
                            state="error",
                            now_value=now_tz(config),
                            error={
                                "timestamp": now_tz(config),
                                "code": "fetch_failed",
                                "message": str(exc),
                            },
                        )
                        logging.error("Data fetcher: error fetching tomorrow schedules: %s", exc)

                if day_ahead_attempted:
                    next_day_ahead_attempt_mono = time.monotonic() + poll_interval_s

            if now_mono >= next_mfrr_attempt_mono:
                previous_mfrr_poll = _snapshot_mfrr_poll_status(shared_data)
                _update_mfrr_poll_status(
                    shared_data,
                    poll_period_s=mfrr_poll_period_s,
                    last_attempt_at=now,
                )
                try:
                    publish_api_fetch_health(shared_data, now_value=now, last_attempt=now)
                    _log_fetch_attempt(
                        "mfrr",
                        f"{today_date}->{tomorrow_date}",
                        today_start,
                        retention_window_end,
                        "periodic mFRR poll",
                        level=logging.DEBUG,
                    )
                    lib_mfrr_schedule = api.get_mfrr_activations(today_start, retention_window_end)
                    lib_mfrr_df = crop_schedule_frame_to_window(
                        api.schedule_to_dataframe(lib_mfrr_schedule, default_q_kvar=0.0),
                        tz,
                        today_start,
                        retention_window_end,
                    )

                    mfrr_map = {}
                    for plant_id in plant_ids:
                        if plant_id == "lib":
                            mfrr_map[plant_id] = lib_mfrr_df
                        elif plant_id == "vrfb":
                            mfrr_map[plant_id] = _zero_schedule_df(lib_mfrr_df.index)
                        else:
                            mfrr_map[plant_id] = _zero_schedule_df(lib_mfrr_df.index)

                    _replace_schedule_maps_and_recompose_total(
                        shared_data,
                        plant_ids,
                        tz,
                        today_start,
                        retention_window_end,
                        mfrr_map=mfrr_map,
                    )
                    _update_mfrr_poll_status(
                        shared_data,
                        poll_period_s=mfrr_poll_period_s,
                        last_success_at=now,
                        last_result="ok",
                        last_error=None,
                        last_points_lib=int(len(lib_mfrr_df)),
                    )
                    _log_mfrr_poll_result(
                        previous_mfrr_poll,
                        result="ok",
                        lib_points=int(len(mfrr_map.get("lib", _empty_schedule_df()))),
                        vrfb_points=int(len(mfrr_map.get("vrfb", _empty_schedule_df()))),
                    )
                except AuthenticationError as exc:
                    _update_mfrr_poll_status(
                        shared_data,
                        poll_period_s=mfrr_poll_period_s,
                        last_result="error",
                        last_error=f"Authentication failed: {exc}",
                        last_points_lib=0,
                    )
                    _log_mfrr_poll_result(previous_mfrr_poll, result="error", lib_points=0, vrfb_points=0)
                    publish_api_fetch_health(
                        shared_data,
                        state="error",
                        now_value=now_tz(config),
                        error={
                            "timestamp": now_tz(config),
                            "code": "auth_failed",
                            "message": f"Authentication failed: {exc}",
                        },
                    )
                    api = None
                    time.sleep(error_backoff_s)
                    continue
                except Exception as exc:
                    _update_mfrr_poll_status(
                        shared_data,
                        poll_period_s=mfrr_poll_period_s,
                        last_result="error",
                        last_error=str(exc),
                        last_points_lib=0,
                    )
                    _log_mfrr_poll_result(previous_mfrr_poll, result="error", lib_points=0, vrfb_points=0)
                    logging.error("Data fetcher: error fetching mFRR schedules: %s", exc)
                finally:
                    next_mfrr_attempt_mono = time.monotonic() + mfrr_poll_period_s
                    _update_mfrr_poll_status(
                        shared_data,
                        poll_period_s=mfrr_poll_period_s,
                        next_scheduled_at=_compute_next_scheduled_at(config, next_mfrr_attempt_mono),
                    )

            _update_status(shared_data, last_attempt=now.isoformat())
            time.sleep(loop_sleep_s)

        except Exception as exc:
            logging.error("Data fetcher: unexpected error: %s", exc)
            publish_api_fetch_health(
                shared_data,
                state="error",
                now_value=now_tz(config),
                error={"timestamp": now_tz(config), "code": "fetch_failed", "message": str(exc)},
            )
            time.sleep(error_backoff_s)

    logging.info("Data fetcher agent stopped.")
