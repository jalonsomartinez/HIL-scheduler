import threading
import time
import unittest
from unittest.mock import patch

import pandas as pd

from measurement_agent import measurement_agent


class _FakePoster:
    force_fail = True
    calls = 0

    def __init__(self, base_url=None, email=None, timezone_name=None):
        self.base_url = base_url
        self.email = email
        self.timezone_name = timezone_name
        self.password = None

    def set_password(self, password):
        self.password = password

    def post_measurement(self, series_id, value, timestamp=None):
        type(self).calls += 1
        if type(self).force_fail:
            raise RuntimeError("forced post failure")
        return {"ok": True}


def _build_shared_data():
    return {
        "lock": threading.Lock(),
        "shutdown_event": threading.Event(),
        "transport_mode": "local",
        "active_schedule_source": "api",
        "api_password": "pw",
        "measurements_filename_by_plant": {"lib": None, "vrfb": None},
        "current_file_path_by_plant": {"lib": None, "vrfb": None},
        "current_file_df_by_plant": {"lib": pd.DataFrame(), "vrfb": pd.DataFrame()},
        "pending_rows_by_file": {},
        "measurements_df": pd.DataFrame(),
        "measurement_post_status": {},
    }


def _build_config():
    return {
        "TIMEZONE_NAME": "Europe/Madrid",
        "PLANT_IDS": ("lib", "vrfb"),
        "PLANTS": {
            "lib": {
                "name": "LIB",
                "model": {"capacity_kwh": 500.0, "poi_voltage_v": 20000.0},
                "measurement_series": {"soc": 4, "p": 6, "q": 7, "v": 8},
            },
            "vrfb": {
                "name": "VRFB",
                "model": {"capacity_kwh": 3000.0, "poi_voltage_v": 20000.0},
                "measurement_series": {"soc": 5, "p": 11, "q": 10, "v": 9},
            },
        },
        "MEASUREMENT_PERIOD_S": 0.2,
        "MEASUREMENTS_WRITE_PERIOD_S": 10.0,
        "ISTENTORE_POST_MEASUREMENTS_IN_API_MODE": True,
        "ISTENTORE_MEASUREMENT_POST_PERIOD_S": 0.2,
        "ISTENTORE_MEASUREMENT_POST_QUEUE_MAXLEN": 200,
        "ISTENTORE_MEASUREMENT_POST_RETRY_INITIAL_S": 0.2,
        "ISTENTORE_MEASUREMENT_POST_RETRY_MAX_S": 0.2,
        "ISTENTORE_BASE_URL": "https://example.invalid",
        "ISTENTORE_EMAIL": "test@example.com",
    }


def _fake_endpoint(*_args, **_kwargs):
    return {
        "host": "localhost",
        "port": 5020,
        "p_setpoint_reg": 1,
        "p_battery_reg": 2,
        "q_setpoint_reg": 3,
        "q_battery_reg": 4,
        "soc_reg": 5,
        "p_poi_reg": 6,
        "q_poi_reg": 7,
        "v_poi_reg": 8,
    }


def _fake_row(_client, _endpoint, measurement_timestamp, _tz, _plant_id):
    return {
        "timestamp": measurement_timestamp,
        "p_setpoint_kw": 100.0,
        "battery_active_power_kw": 100.0,
        "q_setpoint_kvar": 0.0,
        "battery_reactive_power_kvar": 0.0,
        "soc_pu": 0.5,
        "p_poi_kw": 100.0,
        "q_poi_kvar": 0.0,
        "v_poi_pu": 1.0,
    }


def _wait_for(predicate, timeout_s=5.0, interval_s=0.05):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval_s)
    return False


class MeasurementPostingTelemetryTests(unittest.TestCase):
    def test_post_failure_then_recovery_updates_status(self):
        _FakePoster.force_fail = True
        _FakePoster.calls = 0

        config = _build_config()
        shared_data = _build_shared_data()

        with patch("measurement_agent.IstentoreAPI", _FakePoster), patch(
            "measurement_agent.sampling_get_transport_endpoint",
            side_effect=_fake_endpoint,
        ), patch(
            "measurement_agent.sampling_ensure_client",
            return_value=object(),
        ), patch(
            "measurement_agent.sampling_take_measurement",
            side_effect=_fake_row,
        ):
            thread = threading.Thread(target=measurement_agent, args=(config, shared_data), daemon=True)
            thread.start()
            try:
                def failed_state_seen():
                    with shared_data["lock"]:
                        status = shared_data.get("measurement_post_status", {}).get("lib", {})
                        attempt = status.get("last_attempt") or {}
                        return (
                            attempt.get("result") == "failed"
                            and status.get("last_error") is not None
                            and (status.get("pending_queue_count") or 0) >= 1
                            and attempt.get("next_retry_seconds") is not None
                        )

                self.assertTrue(_wait_for(failed_state_seen), "did not observe failed posting telemetry state")

                _FakePoster.force_fail = False

                def success_state_seen():
                    with shared_data["lock"]:
                        status = shared_data.get("measurement_post_status", {}).get("lib", {})
                        attempt = status.get("last_attempt") or {}
                        success = status.get("last_success")
                        return (
                            attempt.get("result") == "success"
                            and success is not None
                            and status.get("last_error") is None
                        )

                self.assertTrue(_wait_for(success_state_seen), "did not observe posting recovery telemetry state")
            finally:
                shared_data["shutdown_event"].set()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
