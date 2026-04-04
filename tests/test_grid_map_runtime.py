import sys
import threading
import types
import unittest
from unittest.mock import patch

import pandas as pd

import grid_map_runtime as gmr


class _FakeNet:
    def __init__(self, *, with_projected_geo=False):
        self.bus = pd.DataFrame(
            {
                "name": ["B1", "B2", "B3"],
                "geo": (
                    [
                        '{"coordinates":[482068.167,4071882.831], "type":"Point"}',
                        '{"coordinates":[482063.167,4071891.83], "type":"Point"}',
                        '{"coordinates":[482008.169,4071837.829], "type":"Point"}',
                    ]
                    if with_projected_geo
                    else [None, None, None]
                ),
            },
            index=[1, 2, 3],
        )
        self.line = pd.DataFrame(
            {
                "from_bus": [1, 2],
                "to_bus": [2, 3],
                "geo": [None, None],
            },
            index=[11, 12],
        )
        self.trafo = pd.DataFrame(
            {
                "hv_bus": [1],
                "lv_bus": [3],
            },
            index=[21],
        )
        self.bus_geodata = pd.DataFrame(columns=["x", "y"])
        self.line_geodata = pd.DataFrame(columns=["coords"])


class _FakeFigure:
    def to_dict(self):
        return {"data": [{"name": "fake"}], "layout": {"title": "fake"}}


class _FakeSimulatorModule:
    def __init__(self, *, with_projected_geo=False):
        self.calls = []
        self.with_projected_geo = with_projected_geo

    def get_base_network_copy(self):
        return _FakeNet(with_projected_geo=self.with_projected_geo)

    def get_metadata(self):
        return {"battery_bus": 2, "hub_bus": 1}

    def run_power_flow(self, **kwargs):
        self.calls.append(dict(kwargs))
        return {
            "selected_timestamp_local": kwargs["timestamp_iso"],
            "selected_timestamp_utc": "2026-04-03T10:00:00+00:00",
            "used_previous_hour_fallback": False,
            "results_tables": {
                "res_bus": pd.DataFrame({"vm_pu": [0.96, 1.02, 1.06]}, index=[1, 2, 3]),
                "res_line": pd.DataFrame({"loading_percent": [55.0, 110.0]}, index=[11, 12]),
            },
        }


def _fake_create_generic_coordinates(net, overwrite=False):
    _ = overwrite
    net.bus_geodata = pd.DataFrame(
        {"x": [0.0, 1.0, 2.0], "y": [0.0, 0.5, 1.0]},
        index=[1, 2, 3],
    )


def _fake_trace_factory(*_args, **_kwargs):
    return {"type": "scatter"}


def _fake_draw_traces(*_args, **_kwargs):
    return _FakeFigure()


class _FakeTransformer:
    def transform(self, x, y):
        return (float(x) / 100000.0) - 10.0, float(y) / 100000.0


def _fake_convert_crs(net, epsg_in=4326, epsg_out=31467):
    _ = (epsg_in, epsg_out)
    transformer = _FakeTransformer()
    geodata = getattr(net, "bus_geodata", None)
    if not isinstance(geodata, pd.DataFrame) or geodata.empty:
        raise RuntimeError("missing bus_geodata")
    converted = []
    for _, row in geodata.iterrows():
        x, y = row["x"], row["y"]
        lon, lat = transformer.transform(x, y)
        converted.append({"x": lon, "y": lat})
    net.bus_geodata = pd.DataFrame(converted, index=geodata.index)


class GridMapRuntimeTests(unittest.TestCase):
    def setUp(self):
        gmr._SIMULATOR_MODULE = None
        self.config = {"TIMEZONE_NAME": "Europe/Madrid"}

    def tearDown(self):
        gmr._SIMULATOR_MODULE = None

    def test_select_lib_power_inputs_prefers_fresh_observed_state(self):
        shared_data = {
            "lock": threading.Lock(),
            "plant_observed_state_by_plant": {
                "lib": {
                    "p_battery_kw": 12.0,
                    "q_battery_kvar": -3.0,
                    "last_success": pd.Timestamp("2026-04-03T12:00:00+02:00"),
                    "stale": False,
                }
            },
            "current_file_df_by_plant": {
                "lib": pd.DataFrame(
                    [
                        {
                            "timestamp": "2026-04-03T11:59:00+02:00",
                            "battery_active_power_kw": 8.0,
                            "battery_reactive_power_kvar": 1.0,
                        }
                    ]
                )
            },
        }

        payload = gmr.select_lib_power_inputs(shared_data, self.config)
        self.assertEqual(payload["source"], "observed_state")
        self.assertEqual(payload["p_kw"], 12.0)
        self.assertEqual(payload["q_kvar"], -3.0)

    def test_select_lib_power_inputs_falls_back_to_measurement_cache(self):
        shared_data = {
            "lock": threading.Lock(),
            "plant_observed_state_by_plant": {
                "lib": {
                    "p_battery_kw": 12.0,
                    "q_battery_kvar": -3.0,
                    "last_success": pd.Timestamp("2026-04-03T12:00:00+02:00"),
                    "stale": True,
                }
            },
            "current_file_df_by_plant": {
                "lib": pd.DataFrame(
                    [
                        {
                            "timestamp": "2026-04-03T11:58:00+02:00",
                            "battery_active_power_kw": 7.5,
                            "battery_reactive_power_kvar": 0.8,
                        }
                    ]
                )
            },
        }

        payload = gmr.select_lib_power_inputs(shared_data, self.config)
        self.assertEqual(payload["source"], "measurement_cache")
        self.assertEqual(payload["p_kw"], 7.5)
        self.assertEqual(payload["q_kvar"], 0.8)

    def test_run_grid_map_power_flow_inverts_active_power_and_converts_units(self):
        fake_simulator = _FakeSimulatorModule()
        gmr._SIMULATOR_MODULE = fake_simulator

        payload = {
            "p_kw": 250.0,
            "q_kvar": -50.0,
            "timestamp": pd.Timestamp("2026-04-03T12:05:00+02:00"),
        }
        result = gmr.run_grid_map_power_flow(payload, self.config)

        self.assertEqual(len(fake_simulator.calls), 1)
        call = fake_simulator.calls[0]
        self.assertEqual(call["battery_p_mw"], -0.25)
        self.assertEqual(call["battery_q_mvar"], -0.05)
        self.assertIn("+02:00", call["timestamp_iso"])
        self.assertEqual(result["battery_input_p_mw"], -0.25)
        self.assertEqual(result["battery_input_q_mvar"], -0.05)

    def test_build_power_flow_summary_and_dynamic_payload_extract_expected_values(self):
        topology_cache = {
            "bus_order": [1, 2, 3],
            "line_order": [11, 12],
        }
        result = {
            "min_voltage_pu": 0.96,
            "max_voltage_pu": 1.06,
            "max_line_loading_pct": 110.0,
            "num_voltage_violations": 1,
            "num_overloaded_lines": 1,
            "results_tables": {
                "res_bus": pd.DataFrame({"vm_pu": [0.96, 1.02, 1.06]}, index=[1, 2, 3]),
                "res_line": pd.DataFrame({"loading_percent": [55.0, 110.0]}, index=[11, 12]),
            },
        }

        summary = gmr.build_power_flow_summary(result)
        dynamic = gmr.build_dynamic_payload(result, topology_cache)

        self.assertEqual(summary["num_voltage_violations"], 1)
        self.assertEqual(summary["num_overloaded_lines"], 1)
        self.assertEqual(dynamic["bus"]["3"]["status"], "violation")
        self.assertEqual(dynamic["line"]["12"]["status"], "overloaded")

    def test_publish_grid_map_error_preserves_last_successful_payload(self):
        shared_data = {"lock": threading.Lock(), gmr.GRID_MAP_STATUS_KEY: gmr.default_grid_map_runtime(5.0)}
        gmr.publish_grid_map_success(
            shared_data,
            now_value=pd.Timestamp("2026-04-03T12:00:00+02:00"),
            input_payload={"source": "observed_state", "timestamp": pd.Timestamp("2026-04-03T11:59:55+02:00")},
            run_payload={
                "requested_timestamp_local": "2026-04-03T12:00:00+02:00",
                "power_flow_result": {
                    "selected_timestamp_local": "2026-04-03T12:00:00+02:00",
                    "selected_timestamp_utc": "2026-04-03T10:00:00+00:00",
                    "used_previous_hour_fallback": False,
                },
                "battery_input_p_kw": 10.0,
                "battery_input_q_kvar": 2.0,
                "battery_input_p_mw": -0.01,
                "battery_input_q_mvar": 0.002,
            },
            summary={"min_voltage_pu": 0.98},
            dynamic_payload={"bus": {"1": {"vm_pu": 0.98}}},
        )

        gmr.publish_grid_map_error(
            shared_data,
            now_value=pd.Timestamp("2026-04-03T12:00:25+02:00"),
            error_text="runtime failed",
            input_payload={"source": "measurement_cache", "p_kw": 9.0, "q_kvar": 1.0},
        )
        runtime_state = gmr.snapshot_grid_map_runtime(shared_data)

        self.assertEqual(runtime_state["last_error"], "runtime failed")
        self.assertEqual(runtime_state["summary"], {"min_voltage_pu": 0.98})
        self.assertEqual(runtime_state["dynamic_payload"], {"bus": {"1": {"vm_pu": 0.98}}})
        self.assertTrue(runtime_state["stale"])

    def test_build_topology_cache_creates_static_geometry_and_initial_figure(self):
        fake_simulator = _FakeSimulatorModule()
        gmr._SIMULATOR_MODULE = fake_simulator

        fake_plotting = types.ModuleType("pandapower.plotting")
        fake_plotting.create_generic_coordinates = _fake_create_generic_coordinates

        fake_geo = types.ModuleType("pandapower.plotting.geo")
        fake_geo.convert_crs = _fake_convert_crs

        fake_plotly = types.ModuleType("pandapower.plotting.plotly")
        fake_plotly.create_bus_trace = _fake_trace_factory
        fake_plotly.create_line_trace = _fake_trace_factory
        fake_plotly.create_trafo_trace = _fake_trace_factory
        fake_plotly.draw_traces = _fake_draw_traces

        fake_pandapower = types.ModuleType("pandapower")
        fake_pandapower.plotting = fake_plotting

        with patch.dict(
            sys.modules,
            {
                "pandapower": fake_pandapower,
                "pandapower.plotting": fake_plotting,
                "pandapower.plotting.geo": fake_geo,
                "pandapower.plotting.plotly": fake_plotly,
            },
        ):
            topology = gmr.build_topology_cache()

        self.assertEqual(topology["bus_order"], [1, 2, 3])
        self.assertEqual(topology["line_order"], [11, 12])
        self.assertEqual(topology["trafo_order"], [21])
        self.assertTrue(bool(topology["initial_figure"]))
        self.assertEqual(topology["coordinate_mode"], "schematic")

    def test_build_topology_cache_detects_projected_coords_and_converts_to_geographic(self):
        fake_simulator = _FakeSimulatorModule(with_projected_geo=True)
        gmr._SIMULATOR_MODULE = fake_simulator

        fake_plotting = types.ModuleType("pandapower.plotting")
        fake_plotting.create_generic_coordinates = _fake_create_generic_coordinates
        fake_geo = types.ModuleType("pandapower.plotting.geo")
        fake_geo.convert_crs = _fake_convert_crs

        with patch.dict(
            sys.modules,
            {
                "pandapower.plotting": fake_plotting,
                "pandapower.plotting.geo": fake_geo,
            },
        ):
            topology = gmr.build_topology_cache()

        self.assertEqual(topology["coordinate_mode"], "geographic")
        self.assertEqual(topology["source_crs"], gmr.GRID_MAP_SOURCE_CRS)
        self.assertEqual(topology["target_crs"], gmr.GRID_MAP_TARGET_CRS)
        self.assertTrue(topology["map_background_enabled"])
        self.assertIn("1", topology["geographic_bus_coords"])
        self.assertIn("11", topology["geographic_line_paths"])
        self.assertIn("21", topology["geographic_trafo_paths"])

    def test_normalize_geojson_components_for_convert_crs_clears_nullable_geo_columns(self):
        net = _FakeNet(with_projected_geo=True)
        net.line.loc[11, "geo"] = '{"coordinates":[[482068.167,4071882.831],[482063.167,4071891.83]], "type":"LineString"}'
        net.line.loc[12, "geo"] = None

        gmr._normalize_geojson_components_for_convert_crs(net)

        self.assertTrue(isinstance(net.bus_geodata, pd.DataFrame) and not net.bus_geodata.empty)
        self.assertTrue(isinstance(net.line_geodata, pd.DataFrame) and not net.line_geodata.empty)
        self.assertTrue(net.bus["geo"].isna().all())
        self.assertTrue(net.line["geo"].isna().all())

    def test_build_topology_cache_falls_back_to_schematic_when_conversion_fails(self):
        fake_simulator = _FakeSimulatorModule(with_projected_geo=True)
        gmr._SIMULATOR_MODULE = fake_simulator

        fake_plotting = types.ModuleType("pandapower.plotting")
        fake_plotting.create_generic_coordinates = _fake_create_generic_coordinates
        fake_geo = types.ModuleType("pandapower.plotting.geo")
        fake_geo.convert_crs = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("no transformer"))

        with patch.dict(
            sys.modules,
            {
                "pandapower.plotting": fake_plotting,
                "pandapower.plotting.geo": fake_geo,
            },
        ):
            topology = gmr.build_topology_cache()

        self.assertEqual(topology["coordinate_mode"], "schematic")
        self.assertFalse(topology["map_background_enabled"])
        self.assertIn("coordinate_conversion_failed", str(topology["map_background_reason"]))

    def test_build_grid_map_figure_reuses_topology_structure(self):
        topology_cache = {
            "metadata": {"battery_bus": 2},
            "coordinate_mode": "schematic",
            "map_background_enabled": False,
            "bounds": {"x_min": 0.0, "x_max": 2.0, "y_min": 0.0, "y_max": 1.0},
            "bus_order": [1, 2, 3],
            "bus_coords": {"1": [0.0, 0.0], "2": [1.0, 0.5], "3": [2.0, 1.0]},
            "line_order": [11, 12],
            "line_paths": {"11": [[0.0, 0.0], [1.0, 0.5]], "12": [[1.0, 0.5], [2.0, 1.0]]},
            "trafo_order": [21],
            "trafo_paths": {"21": [[0.0, 0.0], [2.0, 1.0]]},
        }
        payload_a = {
            "bus": {"1": {"vm_pu": 0.98}, "2": {"vm_pu": 1.0}, "3": {"vm_pu": 1.02}},
            "line": {"11": {"loading_pct": 40.0}, "12": {"loading_pct": 90.0}},
        }
        payload_b = {
            "bus": {"1": {"vm_pu": 0.94}, "2": {"vm_pu": 1.0}, "3": {"vm_pu": 1.06}},
            "line": {"11": {"loading_pct": 40.0}, "12": {"loading_pct": 120.0}},
        }

        fig_a = gmr.build_grid_map_figure(topology_cache, payload_a, uirevision_key="same-key")
        fig_b = gmr.build_grid_map_figure(topology_cache, payload_b, uirevision_key="same-key")

        self.assertEqual(len(fig_a.data), len(fig_b.data))
        self.assertEqual(fig_a.layout.uirevision, "same-key")
        self.assertEqual(fig_b.layout.uirevision, "same-key")

    def test_build_grid_map_figure_uses_geographic_basemap_when_available(self):
        topology_cache = {
            "metadata": {"battery_bus": 2},
            "coordinate_mode": "geographic",
            "source_crs": gmr.GRID_MAP_SOURCE_CRS,
            "target_crs": gmr.GRID_MAP_TARGET_CRS,
            "map_background_enabled": True,
            "geographic_bounds": {"x_min": -5.18, "x_max": -5.17, "y_min": 40.71, "y_max": 40.72},
            "geographic_center": {"lon": -5.175, "lat": 40.715},
            "bus_order": [1, 2, 3],
            "geographic_bus_coords": {"1": [-5.18, 40.71], "2": [-5.175, 40.715], "3": [-5.17, 40.72]},
            "line_order": [11, 12],
            "geographic_line_paths": {"11": [[-5.18, 40.71], [-5.175, 40.715]], "12": [[-5.175, 40.715], [-5.17, 40.72]]},
            "trafo_order": [21],
            "geographic_trafo_paths": {"21": [[-5.18, 40.71], [-5.17, 40.72]]},
        }
        payload = {
            "bus": {"1": {"vm_pu": 0.98}, "2": {"vm_pu": 1.0}, "3": {"vm_pu": 1.02}},
            "line": {"11": {"loading_pct": 40.0}, "12": {"loading_pct": 90.0}},
        }

        fig = gmr.build_grid_map_figure(topology_cache, payload, uirevision_key="geo-key")

        self.assertEqual(fig.layout.uirevision, "geo-key")
        self.assertEqual(fig.layout.map.style, gmr.GRID_MAP_MAP_STYLE)
        self.assertEqual(fig.layout.map.center.lon, -5.175)
        self.assertEqual(fig.layout.map.center.lat, 40.715)
        self.assertGreater(len(fig.data), 0)


if __name__ == "__main__":
    unittest.main()
