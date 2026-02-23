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
        "data_fetcher_status": _status_seed(
            now_value,
            today_fetched=today_fetched,
            tomorrow_fetched=tomorrow_fetched,
            error=error,
        ),
        "api_schedule_df_by_plant": {"lib": pd.DataFrame(), "vrfb": pd.DataFrame()},
    }


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
        self.assertTrue(status["today_fetched"])
        self.assertFalse(status["tomorrow_fetched"])

    def test_tomorrow_fetch_does_not_run_before_gate(self):
        tz = ZoneInfo("Europe/Madrid")
        now_value = datetime(2026, 2, 23, 8, 30, tzinfo=tz)
        config = _build_config(tomorrow_poll_start_time="9:00")
        shared_data = _build_shared_data(now_value, today_fetched=True, tomorrow_fetched=False)

        with self.assertLogs(level="INFO") as logs:
            self._run_once(now_value, config, shared_data)

        self.assertEqual(len(_FakeIstentoreAPI.calls), 0)
        self.assertIn("tomorrow poll gate waiting", "\n".join(logs.output))

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
        self.assertFalse(status["tomorrow_fetched"])
        self.assertIn("Incomplete tomorrow day-ahead data", str(status.get("error")))
        self.assertIn("VRFB=0", str(status.get("error")))
        self.assertEqual(len(lib_df), 1)
        self.assertTrue(vrfb_df.empty)

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
        self.assertTrue(status["tomorrow_fetched"])
        self.assertIsNone(status.get("error"))

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


if __name__ == "__main__":
    unittest.main()
