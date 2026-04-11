import unittest

from dashboard.grid_map import (
    GRID_MAP_INTERACTION_PAUSE_WINDOW_S,
    build_grid_map_status_text,
    is_grid_map_refresh_paused,
    register_grid_map_interaction,
)


class DashboardGridMapTests(unittest.TestCase):
    def test_register_grid_map_interaction_returns_timestamped_state(self):
        state = register_grid_map_interaction({"map.center": {"lon": 1.0}}, now_s=100.0)

        self.assertEqual(state["last_interaction_at_s"], 100.0)
        self.assertEqual(state["last_relayout_keys"], ["map.center"])

    def test_register_grid_map_interaction_ignores_empty_payload(self):
        self.assertIsNone(register_grid_map_interaction(None, now_s=100.0))
        self.assertIsNone(register_grid_map_interaction({}, now_s=100.0))

    def test_is_grid_map_refresh_paused_within_pause_window(self):
        paused = is_grid_map_refresh_paused(
            {"last_interaction_at_s": 100.0},
            now_s=100.0 + GRID_MAP_INTERACTION_PAUSE_WINDOW_S - 0.1,
        )

        self.assertTrue(paused)

    def test_is_grid_map_refresh_paused_after_pause_window_expires(self):
        paused = is_grid_map_refresh_paused(
            {"last_interaction_at_s": 100.0},
            now_s=100.0 + GRID_MAP_INTERACTION_PAUSE_WINDOW_S + 0.1,
        )

        self.assertFalse(paused)

    def test_build_grid_map_status_text_reports_live_and_paused_refresh_states(self):
        live_text = build_grid_map_status_text(
            {
                "state": "ok",
                "topology_ready": True,
                "stale": False,
                "coordinate_mode": "geographic",
                "map_background_mode": "street",
                "refresh_paused": False,
            }
        )
        paused_text = build_grid_map_status_text(
            {
                "state": "ok",
                "topology_ready": True,
                "stale": False,
                "coordinate_mode": "geographic",
                "map_background_mode": "satellite",
                "refresh_paused": True,
            }
        )

        self.assertIn("background=street", live_text)
        self.assertIn("background=satellite", paused_text)
        self.assertIn("map_refresh=live", live_text)
        self.assertIn("map_refresh=paused", paused_text)

if __name__ == "__main__":
    unittest.main()
