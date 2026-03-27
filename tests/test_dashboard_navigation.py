import unittest

from dashboard.navigation import (
    normalize_private_route,
    normalize_public_route,
    page_section_class,
    resolve_menu_open_state,
)


class DashboardNavigationTests(unittest.TestCase):
    def test_private_routes_normalize_with_status_fallback(self):
        self.assertEqual(normalize_private_route(None), "/status")
        self.assertEqual(normalize_private_route("/"), "/status")
        self.assertEqual(normalize_private_route("/status"), "/status")
        self.assertEqual(normalize_private_route("/manual-schedule/"), "/manual-schedule")
        self.assertEqual(normalize_private_route("/unknown"), "/status")

    def test_public_routes_normalize_with_status_fallback(self):
        self.assertEqual(normalize_public_route(""), "/status")
        self.assertEqual(normalize_public_route("/"), "/status")
        self.assertEqual(normalize_public_route("/plots"), "/plots")
        self.assertEqual(normalize_public_route("/plots/"), "/plots")
        self.assertEqual(normalize_public_route("/api-schedule"), "/status")

    def test_route_normalization_ignores_query_and_hash(self):
        self.assertEqual(normalize_private_route("/plots?x=1"), "/plots")
        self.assertEqual(normalize_private_route("/logs#anchor"), "/logs")
        self.assertEqual(normalize_public_route("/plots?x=1#anchor"), "/plots")

    def test_page_section_class_helper(self):
        self.assertEqual(page_section_class(True), "page-section page-section--active")
        self.assertEqual(page_section_class(False), "page-section")

    def test_menu_open_state_helper(self):
        self.assertTrue(
            resolve_menu_open_state(
                trigger_id="dashboard-menu-toggle-btn",
                previous_open=False,
                toggle_trigger_id="dashboard-menu-toggle-btn",
            )
        )
        self.assertFalse(
            resolve_menu_open_state(
                trigger_id="dashboard-menu-overlay",
                previous_open=True,
                toggle_trigger_id="dashboard-menu-toggle-btn",
            )
        )


if __name__ == "__main__":
    unittest.main()
