import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

try:
    import pandas as pd
    from data_fetcher_agent import _reconcile_day_status, data_fetcher_agent
    _IMPORT_ERROR = None
except ModuleNotFoundError as exc:  # pragma: no cover - environment-dependent test skip
    pd = None
    _IMPORT_ERROR = exc


class _FakeIstentoreAPI:
    responses = []
    calls = []

    def __init__(self, base_url=None, email=None, timezone_name=None):
        self.base_url = base_url
        self.email = email
        self.timezone = ZoneInfo(timezone_name or "Europe/Madrid")
        self._password = None

    def set_password(self, password):
        self._password = password

    def get_day_ahead_schedules(self, start_time, end_time):
        type(self).calls.append({"start": start_time, "end": end_time})
        if type(self).responses:
            return type(self).responses.pop(0)
        return {"lib": {}, "vrfb": {}}

    def schedule_to_dataframe(self, schedule, default_q_kvar=0.0):
        if not schedule:
            return pd.DataFrame(
                columns=["datetime", "power_setpoint_kw", "reactive_power_setpoint_kvar"]
            ).set_index("datetime")

        rows = []
        for dt_str, power_kw in schedule.items():
            dt = datetime.fromisoformat(str(dt_str).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            rows.append(
                {
                    "datetime": dt.astimezone(self.timezone),
                    "power_setpoint_kw": float(power_kw),
                    "reactive_power_setpoint_kvar": float(default_q_kvar),
                }
            )
        return pd.DataFrame(rows).set_index("datetime").sort_index()


def _full_schedule_for_day(day_start):
    base_utc = day_start.astimezone(timezone.utc)
    return {
        "lib": {base_utc.isoformat(): 100.0},
        "vrfb": {(base_utc + timedelta(minutes=15)).isoformat(): -50.0},
    }


def _partial_schedule_for_day(day_start):
    base_utc = day_start.astimezone(timezone.utc)
    return {
        "lib": {base_utc.isoformat(): 100.0},
        "vrfb": {},
    }


def _build_config(tomorrow_poll_start_time="17:30"):
    return {
        "PLANT_IDS": ("lib", "vrfb"),
        "ISTENTORE_TOMORROW_POLL_START_TIME": tomorrow_poll_start_time,
        "DATA_FETCHER_PERIOD_S": 0.1,
        "ISTENTORE_BASE_URL": "https://example.invalid",
        "ISTENTORE_EMAIL": "test@example.com",
        "TIMEZONE_NAME": "Europe/Madrid",
    }


def _status_seed(now_value, *, today_fetched=False, tomorrow_fetched=False, error=None):
    today_start = now_value.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start = today_start + timedelta(days=1)
    return {
        "connected": False,
        "today_fetched": bool(today_fetched),
        "tomorrow_fetched": bool(tomorrow_fetched),
        "today_date": today_start.date().isoformat(),
        "tomorrow_date": tomorrow_start.date().isoformat(),
        "today_points": 0,
        "tomorrow_points": 0,
        "today_points_by_plant": {"lib": 0, "vrfb": 0},
        "tomorrow_points_by_plant": {"lib": 0, "vrfb": 0},
        "last_attempt": None,
        "error": error,
    }


def _build_shared_data(now_value, *, today_fetched=False, tomorrow_fetched=False, error=None):
    return {
        "lock": threading.Lock(),
        "shutdown_event": threading.Event(),
        "api_password": "pw",
        "api_connection_runtime": {
            "state": "connected",
            "connected": True,
            "desired_state": "connected",
            "fetch_health": {"state": "unknown", "last_success": None, "last_error": None, "last_attempt": None},
            "posting_health": {"state": "idle", "last_success": None, "last_error": None, "last_attempt": None},
        },
        "data_fetcher_status": _status_seed(
            now_value,
            today_fetched=today_fetched,
            tomorrow_fetched=tomorrow_fetched,
            error=error,
        ),
        "api_schedule_df_by_plant": {"lib": pd.DataFrame(), "vrfb": pd.DataFrame()},
    }


def _schedule_df(*timestamps):
    rows = []
    for idx, ts in enumerate(timestamps, start=1):
        rows.append(
            {
                "datetime": ts,
                "power_setpoint_kw": float(idx),
                "reactive_power_setpoint_kvar": 0.0,
            }
        )
    return pd.DataFrame(rows).set_index("datetime").sort_index()


class _StopAfterPollSleep:
    def __init__(self, shutdown_event):
        self.shutdown_event = shutdown_event
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(float(seconds))
        self.shutdown_event.set()


@unittest.skipIf(pd is None, f"pandas unavailable: {_IMPORT_ERROR}")
class DataFetcherAgentTests(unittest.TestCase):
    def setUp(self):
        _FakeIstentoreAPI.responses = []
        _FakeIstentoreAPI.calls = []

    def _run_once(self, now_value, config, shared_data):
        fake_sleep = _StopAfterPollSleep(shared_data["shutdown_event"])
        with patch("data_fetcher_agent.IstentoreAPI", _FakeIstentoreAPI), patch(
            "data_fetcher_agent.now_tz",
            return_value=now_value,
        ), patch(
            "data_fetcher_agent.time.sleep",
            side_effect=fake_sleep,
        ):
            data_fetcher_agent(config, shared_data)
        return fake_sleep

    def test_today_fetch_runs_before_tomorrow_gate_when_today_missing(self):
        tz = ZoneInfo("Europe/Madrid")
        now_value = datetime(2026, 2, 23, 8, 0, tzinfo=tz)
        config = _build_config(tomorrow_poll_start_time="9:00")
        shared_data = _build_shared_data(now_value, today_fetched=False, tomorrow_fetched=False)
        today_start = now_value.replace(hour=0, minute=0, second=0, microsecond=0)
        _FakeIstentoreAPI.responses = [_full_schedule_for_day(today_start)]

        with self.assertLogs(level="INFO") as logs:
            self._run_once(now_value, config, shared_data)

        self.assertEqual(len(_FakeIstentoreAPI.calls), 1)
        self.assertEqual(_FakeIstentoreAPI.calls[0]["start"].date(), today_start.date())
        joined = "\n".join(logs.output)
        self.assertIn("purpose=today", joined)
        self.assertIn("tomorrow poll gate waiting", joined)
        with shared_data["lock"]:
            status = dict(shared_data["data_fetcher_status"])
            api_runtime = dict(shared_data["api_connection_runtime"])
        self.assertTrue(status["today_fetched"])
        self.assertFalse(status["tomorrow_fetched"])
        self.assertEqual(api_runtime["fetch_health"]["state"], "ok")

    def test_tomorrow_fetch_does_not_run_before_gate(self):
        tz = ZoneInfo("Europe/Madrid")
        now_value = datetime(2026, 2, 23, 8, 30, tzinfo=tz)
        config = _build_config(tomorrow_poll_start_time="9:00")
        shared_data = _build_shared_data(now_value, today_fetched=True, tomorrow_fetched=False)

        with self.assertLogs(level="INFO") as logs:
            self._run_once(now_value, config, shared_data)

        self.assertEqual(len(_FakeIstentoreAPI.calls), 0)
        self.assertIn("tomorrow poll gate waiting", "\n".join(logs.output))

    def test_intentional_disconnect_gate_skips_fetch_and_publishes_disabled_health(self):
        tz = ZoneInfo("Europe/Madrid")
        now_value = datetime(2026, 2, 23, 8, 30, tzinfo=tz)
        config = _build_config(tomorrow_poll_start_time="9:00")
        shared_data = _build_shared_data(now_value, today_fetched=False, tomorrow_fetched=False)
        with shared_data["lock"]:
            shared_data["api_connection_runtime"]["state"] = "disconnected"
            shared_data["api_connection_runtime"]["connected"] = False
            shared_data["api_connection_runtime"]["desired_state"] = "disconnected"

        self._run_once(now_value, config, shared_data)

        self.assertEqual(len(_FakeIstentoreAPI.calls), 0)
        with shared_data["lock"]:
            api_runtime = dict(shared_data["api_connection_runtime"])
        self.assertEqual(api_runtime["fetch_health"]["state"], "disabled")

    def test_tomorrow_fetch_runs_at_or_after_gate_with_non_padded_time(self):
        tz = ZoneInfo("Europe/Madrid")
        now_value = datetime(2026, 2, 23, 10, 0, tzinfo=tz)
        config = _build_config(tomorrow_poll_start_time="9:00")
        shared_data = _build_shared_data(now_value, today_fetched=True, tomorrow_fetched=False)
        tomorrow_start = now_value.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        _FakeIstentoreAPI.responses = [_full_schedule_for_day(tomorrow_start)]

        with self.assertLogs(level="INFO") as logs:
            self._run_once(now_value, config, shared_data)

        self.assertEqual(len(_FakeIstentoreAPI.calls), 1)
        self.assertEqual(_FakeIstentoreAPI.calls[0]["start"].date(), tomorrow_start.date())
        joined = "\n".join(logs.output)
        self.assertIn("tomorrow poll gate eligible", joined)
        self.assertIn("purpose=tomorrow", joined)
        with shared_data["lock"]:
            status = dict(shared_data["data_fetcher_status"])
        self.assertTrue(status["tomorrow_fetched"])

    def test_partial_tomorrow_fetch_publishes_partial_and_sets_error(self):
        tz = ZoneInfo("Europe/Madrid")
        now_value = datetime(2026, 2, 23, 10, 0, tzinfo=tz)
        config = _build_config(tomorrow_poll_start_time="09:00")
        shared_data = _build_shared_data(now_value, today_fetched=True, tomorrow_fetched=False)
        tomorrow_start = now_value.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        _FakeIstentoreAPI.responses = [_partial_schedule_for_day(tomorrow_start)]

        with self.assertLogs(level="INFO") as logs:
            self._run_once(now_value, config, shared_data)

        joined = "\n".join(logs.output)
        self.assertIn("purpose=tomorrow", joined)
        self.assertIn("tomorrow schedules fetched partial", joined)
        with shared_data["lock"]:
            status = dict(shared_data["data_fetcher_status"])
            lib_df = shared_data["api_schedule_df_by_plant"]["lib"].copy()
            vrfb_df = shared_data["api_schedule_df_by_plant"]["vrfb"].copy()
            api_runtime = dict(shared_data["api_connection_runtime"])
        self.assertFalse(status["tomorrow_fetched"])
        self.assertIn("Incomplete tomorrow day-ahead data", str(status.get("error")))
        self.assertIn("VRFB=0", str(status.get("error")))
        self.assertEqual(len(lib_df), 1)
        self.assertTrue(vrfb_df.empty)
        self.assertEqual(api_runtime["fetch_health"]["state"], "error")

    def test_complete_tomorrow_fetch_clears_error(self):
        tz = ZoneInfo("Europe/Madrid")
        now_value = datetime(2026, 2, 23, 10, 0, tzinfo=tz)
        config = _build_config(tomorrow_poll_start_time="09:00")
        shared_data = _build_shared_data(now_value, today_fetched=True, tomorrow_fetched=False, error="old error")
        tomorrow_start = now_value.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        _FakeIstentoreAPI.responses = [_full_schedule_for_day(tomorrow_start)]

        self._run_once(now_value, config, shared_data)

        with shared_data["lock"]:
            status = dict(shared_data["data_fetcher_status"])
            api_runtime = dict(shared_data["api_connection_runtime"])
        self.assertTrue(status["tomorrow_fetched"])
        self.assertIsNone(status.get("error"))
        self.assertEqual(api_runtime["fetch_health"]["state"], "ok")

    def test_rollover_promotes_previous_tomorrow_status_to_today(self):
        shared_data = {
            "lock": threading.Lock(),
            "data_fetcher_status": {
                "today_date": "2026-02-22",
                "tomorrow_date": "2026-02-23",
                "today_fetched": False,
                "tomorrow_fetched": True,
                "today_points": 0,
                "tomorrow_points": 2,
                "today_points_by_plant": {"lib": 0, "vrfb": 0},
                "tomorrow_points_by_plant": {"lib": 1, "vrfb": 1},
            },
        }

        _reconcile_day_status(
            shared_data,
            today_date="2026-02-23",
            tomorrow_date="2026-02-24",
            plant_ids=("lib", "vrfb"),
        )

        with shared_data["lock"]:
            status = dict(shared_data["data_fetcher_status"])
        self.assertTrue(status["today_fetched"])
        self.assertEqual(status["today_points"], 2)
        self.assertEqual(status["today_points_by_plant"], {"lib": 1, "vrfb": 1})
        self.assertFalse(status["tomorrow_fetched"])
        self.assertEqual(status["tomorrow_points"], 0)
        self.assertEqual(status["today_date"], "2026-02-23")
        self.assertEqual(status["tomorrow_date"], "2026-02-24")

    def test_prunes_api_schedule_frames_to_current_and_next_day_window(self):
        tz = ZoneInfo("Europe/Madrid")
        now_value = datetime(2026, 2, 23, 12, 0, tzinfo=tz)
        config = _build_config(tomorrow_poll_start_time="09:00")
        shared_data = _build_shared_data(now_value, today_fetched=True, tomorrow_fetched=True)

        today_start = now_value.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow_start = today_start + timedelta(days=1)
        window_end = today_start + timedelta(days=2)
        seed_df = _schedule_df(
            today_start - timedelta(minutes=15),
            today_start,
            tomorrow_start + timedelta(minutes=15),
            window_end,
        )

        with shared_data["lock"]:
            shared_data["api_schedule_df_by_plant"] = {"lib": seed_df.copy(), "vrfb": seed_df.copy()}

        self._run_once(now_value, config, shared_data)

        with shared_data["lock"]:
            lib_df = shared_data["api_schedule_df_by_plant"]["lib"].copy()
            vrfb_df = shared_data["api_schedule_df_by_plant"]["vrfb"].copy()

        for df in (lib_df, vrfb_df):
            self.assertEqual(len(df), 2)
            self.assertTrue((df.index >= today_start).all())
            self.assertTrue((df.index < window_end).all())
            self.assertNotIn(window_end, set(df.index))

    def test_today_refetch_preserves_existing_tomorrow_rows_in_window(self):
        tz = ZoneInfo("Europe/Madrid")
        now_value = datetime(2026, 2, 23, 8, 0, tzinfo=tz)
        config = _build_config(tomorrow_poll_start_time="09:00")
        shared_data = _build_shared_data(now_value, today_fetched=False, tomorrow_fetched=True)

        today_start = now_value.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow_start = today_start + timedelta(days=1)
        with shared_data["lock"]:
            shared_data["api_schedule_df_by_plant"] = {
                "lib": _schedule_df(tomorrow_start + timedelta(minutes=30)),
                "vrfb": _schedule_df(tomorrow_start + timedelta(minutes=45)),
            }

        _FakeIstentoreAPI.responses = [_full_schedule_for_day(today_start)]
        self._run_once(now_value, config, shared_data)

        with shared_data["lock"]:
            lib_df = shared_data["api_schedule_df_by_plant"]["lib"].copy()
            vrfb_df = shared_data["api_schedule_df_by_plant"]["vrfb"].copy()

        self.assertEqual(len(_FakeIstentoreAPI.calls), 1)
        self.assertEqual(len(lib_df), 2)
        self.assertEqual(len(vrfb_df), 2)
        self.assertTrue(any(ts.date() == tomorrow_start.date() for ts in lib_df.index))
        self.assertTrue(any(ts.date() == tomorrow_start.date() for ts in vrfb_df.index))

    def test_tomorrow_fetch_merge_remains_bounded_on_repeated_runs(self):
        tz = ZoneInfo("Europe/Madrid")
        now_value = datetime(2026, 2, 23, 10, 0, tzinfo=tz)
        config = _build_config(tomorrow_poll_start_time="09:00")
        shared_data = _build_shared_data(now_value, today_fetched=True, tomorrow_fetched=False)

        today_start = now_value.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow_start = today_start + timedelta(days=1)
        window_end = today_start + timedelta(days=2)
        with shared_data["lock"]:
            shared_data["api_schedule_df_by_plant"] = {
                "lib": _schedule_df(
                    today_start - timedelta(minutes=15),
                    today_start + timedelta(minutes=15),
                    window_end + timedelta(minutes=15),
                ),
                "vrfb": _schedule_df(
                    today_start - timedelta(minutes=30),
                    today_start + timedelta(minutes=30),
                    window_end + timedelta(minutes=30),
                ),
            }

        _FakeIstentoreAPI.responses = [_full_schedule_for_day(tomorrow_start)]
        self._run_once(now_value, config, shared_data)

        with shared_data["lock"]:
            first_lib_df = shared_data["api_schedule_df_by_plant"]["lib"].copy()
            first_vrfb_df = shared_data["api_schedule_df_by_plant"]["vrfb"].copy()
            shared_data["shutdown_event"].clear()
            shared_data["data_fetcher_status"]["tomorrow_fetched"] = False

        _FakeIstentoreAPI.responses = [_full_schedule_for_day(tomorrow_start)]
        self._run_once(now_value, config, shared_data)

        with shared_data["lock"]:
            second_lib_df = shared_data["api_schedule_df_by_plant"]["lib"].copy()
            second_vrfb_df = shared_data["api_schedule_df_by_plant"]["vrfb"].copy()

        for first_df, second_df in ((first_lib_df, second_lib_df), (first_vrfb_df, second_vrfb_df)):
            self.assertEqual(len(first_df), len(second_df))
            self.assertTrue((second_df.index >= today_start).all())
            self.assertTrue((second_df.index < window_end).all())
            self.assertNotIn(window_end, set(second_df.index))


if __name__ == "__main__":
    unittest.main()
