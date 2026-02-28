import base64
import importlib.util
import inspect
import os
import queue
import tempfile
import threading
import unittest
from contextlib import chdir

import pandas as pd

from config_loader import load_config
from dashboard.public_agent import build_public_history_slice, build_public_readonly_app
from measurement.storage import MEASUREMENT_COLUMNS


def _row(ts, p_kw):
    return {
        "timestamp": ts,
        "p_setpoint_kw": p_kw,
        "battery_active_power_kw": p_kw,
        "q_setpoint_kvar": 0.0,
        "battery_reactive_power_kvar": 0.0,
        "soc_pu": 0.5,
        "p_poi_kw": p_kw,
        "q_poi_kvar": 0.0,
        "v_poi_kV": 1.0,
    }


def _minimal_shared_data():
    return {
        "lock": threading.Lock(),
        "shutdown_event": threading.Event(),
        "control_command_queue": queue.Queue(maxsize=8),
        "settings_command_queue": queue.Queue(maxsize=8),
    }


def _index_components_by_id(component, output):
    if component is None:
        return
    component_id = getattr(component, "id", None)
    if component_id is not None:
        output[component_id] = component
    children = getattr(component, "children", None)
    if isinstance(children, (list, tuple)):
        for child in children:
            _index_components_by_id(child, output)
        return
    _index_components_by_id(children, output)


class PublicDashboardAgentTests(unittest.TestCase):
    def test_public_module_does_not_reference_enqueue_command_helpers(self):
        import dashboard.public_agent as public_agent_module

        source = inspect.getsource(public_agent_module)
        self.assertNotIn("enqueue_control_command", source)
        self.assertNotIn("enqueue_settings_command", source)

    def test_build_public_history_slice_ignores_client_index_payload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with chdir(tmpdir):
                os.makedirs("data", exist_ok=True)
                df = pd.DataFrame(
                    [
                        _row("2026-02-21T13:10:00+01:00", 1.0),
                        _row("2026-02-21T13:11:00+01:00", 2.0),
                    ],
                    columns=MEASUREMENT_COLUMNS,
                )
                df.to_csv("data/20260221_lib.csv", index=False)

                config = load_config(os.path.join(os.path.dirname(__file__), "..", "config.yaml"))
                tz = config["SCHEDULE_START_TIME"].tzinfo
                malicious = {
                    "has_data": True,
                    "global_start_ms": 0,
                    "global_end_ms": 1,
                    "files_by_plant": {
                        "lib": [{"path": "/etc/passwd", "start_ms": 0, "end_ms": 1, "rows": 1}],
                        "vrfb": [],
                    },
                }

                result = build_public_history_slice(
                    "data",
                    {"lib": "lib", "vrfb": "vrfb"},
                    plant_id="lib",
                    selected_range=[0, 1],
                    tz=tz,
                    client_index_data=malicious,
                )

                self.assertTrue(result["index_data"].get("has_data"))
                self.assertFalse(result["measurements_df"].empty)
                self.assertTrue(
                    all(item.get("path", "").startswith("data/") for item in result["index_data"]["files_by_plant"]["lib"])
                )

    def test_public_app_http_reads_do_not_mutate_command_queues(self):
        config = load_config("config.yaml")
        config["DASHBOARD_PUBLIC_READONLY_AUTH_MODE"] = "none"
        shared_data = _minimal_shared_data()
        shared_data["control_command_queue"].put({"id": "cmd-1"})
        shared_data["settings_command_queue"].put({"id": "set-1"})

        app = build_public_readonly_app(config, shared_data)
        client = app.server.test_client()

        before_control = shared_data["control_command_queue"].qsize()
        before_settings = shared_data["settings_command_queue"].qsize()

        self.assertEqual(client.get("/").status_code, 200)
        self.assertEqual(client.get("/_dash-layout").status_code, 200)
        self.assertEqual(client.get("/_dash-dependencies").status_code, 200)

        self.assertEqual(shared_data["control_command_queue"].qsize(), before_control)
        self.assertEqual(shared_data["settings_command_queue"].qsize(), before_settings)

    def test_public_status_controls_render_readonly_buttons(self):
        config = load_config("config.yaml")
        config["DASHBOARD_PUBLIC_READONLY_AUTH_MODE"] = "none"
        app = build_public_readonly_app(config, _minimal_shared_data())

        by_id = {}
        _index_components_by_id(app.layout, by_id)

        button_ids = [
            "public-start-lib",
            "public-stop-lib",
            "public-dispatch-enable-lib",
            "public-dispatch-disable-lib",
            "public-record-lib",
            "public-record-stop-lib",
            "public-start-vrfb",
            "public-stop-vrfb",
            "public-dispatch-enable-vrfb",
            "public-dispatch-disable-vrfb",
            "public-record-vrfb",
            "public-record-stop-vrfb",
        ]
        for button_id in button_ids:
            self.assertIn(button_id, by_id)
            self.assertTrue(bool(getattr(by_id[button_id], "disabled", False)))

        indicator_ids = [
            "public-api-connection-indicator",
            "public-api-today-indicator",
            "public-api-tomorrow-indicator",
            "public-transport-text",
            "public-error-text",
            "public-plant-summary-table",
        ]
        for indicator_id in indicator_ids:
            self.assertIn(indicator_id, by_id)

        self.assertNotIn("public-status-lib", by_id)
        self.assertNotIn("public-status-vrfb", by_id)

    def test_public_basic_auth_challenges_unauthenticated_requests(self):
        if importlib.util.find_spec("dash_auth") is None:
            self.skipTest("dash_auth is not installed in this environment")

        prev_user = os.getenv("HIL_PUBLIC_DASH_USER")
        prev_pass = os.getenv("HIL_PUBLIC_DASH_PASS")
        os.environ["HIL_PUBLIC_DASH_USER"] = "public"
        os.environ["HIL_PUBLIC_DASH_PASS"] = "secret"
        try:
            config = load_config("config.yaml")
            config["DASHBOARD_PUBLIC_READONLY_AUTH_MODE"] = "basic"
            shared_data = _minimal_shared_data()

            app = build_public_readonly_app(config, shared_data)
            client = app.server.test_client()

            unauth = client.get("/")
            self.assertEqual(unauth.status_code, 401)

            token = base64.b64encode(b"public:secret").decode("ascii")
            auth = client.get("/", headers={"Authorization": f"Basic {token}"})
            self.assertEqual(auth.status_code, 200)
        finally:
            if prev_user is None:
                os.environ.pop("HIL_PUBLIC_DASH_USER", None)
            else:
                os.environ["HIL_PUBLIC_DASH_USER"] = prev_user
            if prev_pass is None:
                os.environ.pop("HIL_PUBLIC_DASH_PASS", None)
            else:
                os.environ["HIL_PUBLIC_DASH_PASS"] = prev_pass


if __name__ == "__main__":
    unittest.main()
