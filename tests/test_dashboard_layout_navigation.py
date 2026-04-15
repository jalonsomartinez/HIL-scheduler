import queue
import threading
import unittest
from datetime import datetime, timezone

from config_loader import load_config

try:
    from dashboard.layout import build_dashboard_layout
    from dashboard.public_agent import build_public_readonly_app

    _IMPORT_ERROR = None
except ModuleNotFoundError as exc:  # pragma: no cover - environment-dependent test skip
    build_dashboard_layout = None
    build_public_readonly_app = None
    _IMPORT_ERROR = exc


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


def _minimal_shared_data():
    return {
        "lock": threading.Lock(),
        "shutdown_event": threading.Event(),
        "control_command_queue": queue.Queue(maxsize=8),
        "settings_command_queue": queue.Queue(maxsize=8),
    }


@unittest.skipIf(build_dashboard_layout is None, f"dashboard deps unavailable: {_IMPORT_ERROR}")
class DashboardLayoutNavigationTests(unittest.TestCase):
    def test_private_layout_renders_sidebar_navigation_shell(self):
        layout = build_dashboard_layout(
            {"MEASUREMENT_PERIOD_S": 5.0},
            ("lib", "vrfb"),
            lambda plant_id: plant_id.upper(),
            brand_logo_src="/assets/brand/logo.png",
            initial_transport="local",
            initial_posting_enabled=True,
            now_value=datetime(2026, 3, 27, 12, 0, tzinfo=timezone.utc),
        )

        by_id = {}
        _index_components_by_id(layout, by_id)

        expected_ids = [
            "dashboard-url",
            "dashboard-route-store",
            "dashboard-menu-open-store",
            "dashboard-menu-toggle-btn",
            "dashboard-side-menu",
            "dashboard-menu-overlay",
            "menu-link-status",
            "menu-link-plots",
            "menu-link-grid-map",
            "menu-link-manual-schedule",
            "menu-link-api-schedule",
            "menu-link-logs",
            "page-private-status",
            "page-private-plots",
            "page-private-grid-map",
            "page-private-manual-schedule",
            "page-private-api-schedule",
            "page-private-logs",
            "plots-grid-map-history-graph",
            "plots-grid-map-nobat-history-graph",
            "plots-grid-map-impact-history-graph",
            "grid-map-status",
            "grid-map-summary",
            "grid-map-meta",
            "grid-map-figure",
            "grid-map-scenario-toggle",
            "grid-map-render-state",
            "grid-map-interaction-state",
            "grid-map-startup-fit-state",
        ]
        for component_id in expected_ids:
            self.assertIn(component_id, by_id)
        self.assertNotIn("main-tabs", by_id)

    def test_public_layout_renders_sidebar_navigation_shell(self):
        config = load_config("config.yaml")
        config["DASHBOARD_PUBLIC_READONLY_AUTH_MODE"] = "none"
        app = build_public_readonly_app(config, _minimal_shared_data())

        by_id = {}
        _index_components_by_id(app.layout, by_id)

        expected_ids = [
            "public-url",
            "public-route-store",
            "public-menu-open-store",
            "public-menu-toggle-btn",
            "public-side-menu",
            "public-menu-overlay",
            "public-menu-link-status",
            "public-menu-link-plots",
            "public-menu-link-grid-map",
            "page-public-status",
            "page-public-plots",
            "page-public-grid-map",
            "public-plots-grid-map-history-graph",
            "public-plots-grid-map-nobat-history-graph",
            "public-plots-grid-map-impact-history-graph",
            "public-grid-map-status",
            "public-grid-map-summary",
            "public-grid-map-meta",
            "public-grid-map-figure",
            "public-grid-map-scenario-toggle",
            "public-grid-map-render-state",
            "public-grid-map-interaction-state",
            "public-grid-map-startup-fit-state",
        ]
        for component_id in expected_ids:
            self.assertIn(component_id, by_id)
        self.assertNotIn("public-main-tabs", by_id)


if __name__ == "__main__":
    unittest.main()
