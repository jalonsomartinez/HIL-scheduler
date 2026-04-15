import unittest

from dash.development.base_component import Component

from dashboard.grid_map import (
    GRID_MAP_INTERACTION_PAUSE_WINDOW_S,
    GRID_MAP_SCENARIO_WITHOUT_BATTERY,
    build_grid_map_page,
    build_grid_map_display_runtime,
    build_grid_map_summary_cards,
    build_grid_map_status_text,
    is_grid_map_refresh_paused,
    register_grid_map_interaction,
    resolve_grid_map_scenario_key,
)


def _collect_ids(component, output):
    if component is None:
        return
    component_id = getattr(component, "id", None)
    if component_id is not None:
        output.add(component_id)
    children = getattr(component, "children", None)
    if isinstance(children, (list, tuple)):
        for child in children:
            _collect_ids(child, output)
    elif isinstance(children, Component):
        _collect_ids(children, output)


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

    def test_build_grid_map_summary_cards_includes_battery_voltage(self):
        cards = build_grid_map_summary_cards(
            {
                "battery_voltage_pu": 1.0234,
                "min_voltage_pu": 0.98,
                "max_voltage_pu": 1.02,
                "num_voltage_violations": 0,
                "max_line_loading_pct": 75.0,
                "num_overloaded_lines": 0,
            }
        )

        first_card_children = list(cards[0].children)
        self.assertEqual(first_card_children[0].children, "Battery Voltage")
        self.assertEqual(first_card_children[1].children, "1.0234 pu")

    def test_build_grid_map_page_places_status_below_graph(self):
        page = build_grid_map_page(prefix="", title="Grid Map")
        children = list(page.children)
        child_ids = set()
        _collect_ids(page, child_ids)
        graph_index = next(index for index, child in enumerate(children) if getattr(child, "id", None) == "grid-map-figure")
        meta_block_index = next(
            index for index, child in enumerate(children) if "grid-map-meta-block" in str(getattr(child, "className", ""))
        )
        meta_block = children[meta_block_index]
        meta_children = list(meta_block.children)
        status_index = next(index for index, child in enumerate(meta_children) if getattr(child, "id", None) == "grid-map-status")
        meta_index = next(index for index, child in enumerate(meta_children) if getattr(child, "id", None) == "grid-map-meta")

        self.assertGreater(meta_block_index, graph_index)
        self.assertLess(status_index, meta_index)
        self.assertIn("grid-map-startup-fit-state", child_ids)
        self.assertIn("grid-map-scenario-toggle", child_ids)

    def test_resolve_grid_map_scenario_key_uses_checkbox_selection(self):
        self.assertEqual(resolve_grid_map_scenario_key([]), "with_battery")
        self.assertEqual(
            resolve_grid_map_scenario_key([GRID_MAP_SCENARIO_WITHOUT_BATTERY]),
            GRID_MAP_SCENARIO_WITHOUT_BATTERY,
        )

    def test_build_grid_map_display_runtime_uses_selected_scenario_payload(self):
        runtime = build_grid_map_display_runtime(
            {
                "summary": {"battery_voltage_pu": 1.01},
                "dynamic_payload": {"bus": {"1": {"vm_pu": 1.01}}},
                "scenario_results": {
                    "with_battery": {"summary": {"battery_voltage_pu": 1.01}, "dynamic_payload": {"kind": "with"}},
                    "without_battery": {"summary": {"battery_voltage_pu": 0.99}, "dynamic_payload": {"kind": "without"}},
                },
            },
            GRID_MAP_SCENARIO_WITHOUT_BATTERY,
        )

        self.assertEqual(runtime["summary"], {"battery_voltage_pu": 0.99})
        self.assertEqual(runtime["dynamic_payload"], {"kind": "without"})
        self.assertEqual(runtime["display_scenario_label"], "Without Battery")

if __name__ == "__main__":
    unittest.main()
