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
                "vn_kv": [20.0, 20.0, 20.0],
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
    def __init__(self, *, data=None, layout=None):
        self._data = list(data or [])
        self._layout = dict(layout or {})

    def to_dict(self):
        return {"data": list(self._data), "layout": dict(self._layout)}


class _FakeSimulatorModule:
    def __init__(self, *, with_projected_geo=False, include_voltage_kv=True):
        self.calls = []
        self.with_projected_geo = with_projected_geo
        self.include_voltage_kv = include_voltage_kv

    def get_base_network_copy(self):
        return _FakeNet(with_projected_geo=self.with_projected_geo)

    def get_metadata(self):
        return {"battery_bus": 2, "hub_bus": 1}

    def run_power_flow(self, **kwargs):
        self.calls.append(dict(kwargs))
        result = {
            "selected_timestamp_local": kwargs["timestamp_iso"],
            "selected_timestamp_utc": "2026-04-03T10:00:00+00:00",
            "used_previous_hour_fallback": False,
            "battery_bus_vm_pu": 1.02,
            "results_tables": {
                "res_bus": pd.DataFrame({"vm_pu": [0.96, 1.02, 1.06]}, index=[1, 2, 3]),
                "res_line": pd.DataFrame({"loading_percent": [55.0, 110.0]}, index=[11, 12]),
            },
        }
        if self.include_voltage_kv:
            result["battery_bus_vm_kv"] = 20.4
        return result


class _FakeAssetsOnlySimulatorModule:
    def __init__(self):
        self.calls = []
        self.asset_loads = 0

    def _load_assets(self):
        self.asset_loads += 1
        return {"base_net": _FakeNet(), "metadata": {"battery_bus": 2, "hub_bus": 1}}

    def run_power_flow(self, **kwargs):
        self.calls.append(dict(kwargs))
        return {
            "selected_timestamp_local": kwargs["timestamp_iso"],
            "selected_timestamp_utc": "2026-04-03T10:00:00+00:00",
            "used_previous_hour_fallback": False,
            "battery_bus_vm_pu": 1.02,
            "battery_bus_vm_kv": 20.4,
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


def _fake_bus_trace(net, buses=None, infofunc=None, cmap_vals=None, trace_name="buses", **_kwargs):
    buses = list(buses or [])
    return {
        "type": "scatter",
        "mode": "markers",
        "name": trace_name,
        "x": list(range(len(buses))),
        "y": [0.0] * len(buses),
        "text": [str(infofunc.loc[idx]) for idx in buses] if isinstance(infofunc, pd.Series) else [],
        "marker": {"color": list(cmap_vals or [])},
    }


def _fake_line_trace(net, lines=None, infofunc=None, cmap_vals=None, trace_name="lines", **_kwargs):
    lines = list(lines or [])
    cmap_vals = list(cmap_vals or [])
    traces = []
    for idx, line_id in enumerate(lines):
        loading = cmap_vals[idx] if idx < len(cmap_vals) else 0.0
        color = "#00945a"
        if float(loading) > 100.0:
            color = "#d93838"
        elif float(loading) >= 80.0:
            color = "#d28c00"
        traces.append(
            {
                "type": "scatter",
                "mode": "lines",
                "name": trace_name,
                "x": [float(idx), float(idx) + 0.5],
                "y": [0.0, 1.0],
                "text": str(infofunc.loc[line_id]) if isinstance(infofunc, pd.Series) else f"Line {line_id}",
                "line": {"color": color},
            }
        )
    traces.append(
        {
            "type": "scatter",
            "mode": "markers",
            "name": f"{trace_name}-center",
            "x": [0.0] * len(lines),
            "y": [0.0] * len(lines),
            "text": [str(infofunc.loc[idx]) for idx in lines] if isinstance(infofunc, pd.Series) else [],
            "marker": {"size": 0},
        }
    )
    return traces


def _fake_trafo_trace(net, trafos=None, infofunc=None, trace_name="trafos", **_kwargs):
    trafos = list(trafos or [])
    return [
        {
            "type": "scatter",
            "mode": "lines",
            "name": trace_name,
            "x": [0.0, 1.0],
            "y": [0.0, 1.0],
            "text": str(infofunc.loc[trafos[0]]) if isinstance(infofunc, pd.Series) and trafos else "Transformer",
        }
    ]


def _fake_draw_traces(traces, on_map=False, map_style="basic", **_kwargs):
    normalized = []
    for trace in list(traces or []):
        current = dict(trace)
        if on_map:
            if "x" in current:
                current["lon"] = current.pop("x")
            if "y" in current:
                current["lat"] = current.pop("y")
            current["type"] = "scattermap"
        normalized.append(current)
    layout = {"title": "fake", "meta": {}, "map": {"style": map_style}} if on_map else {"title": "fake", "meta": {}}
    return _FakeFigure(data=normalized, layout=layout)


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


def _grid_map_write_config():
    point = {
        "name": "v_poi_write",
        "address": 400,
        "format": "uint16",
        "access": "rw",
        "unit": "V",
        "eng_per_count": 1.0,
    }
    return {
        "TIMEZONE_NAME": "Europe/Madrid",
        "PLANT_IDS": ("lib", "vrfb"),
        "PLANTS": {
            "lib": {
                "modbus": {
                    "local": {
                        "host": "127.0.0.1",
                        "port": 15020,
                        "byte_order": "big",
                        "word_order": "msw_first",
                        "points": {"v_poi_write": dict(point)},
                    },
                    "remote": {
                        "host": "10.0.0.21",
                        "port": 502,
                        "byte_order": "big",
                        "word_order": "msw_first",
                        "points": {"v_poi_write": dict(point, address=500)},
                    },
                }
            },
            "vrfb": {
                "modbus": {
                    "local": {
                        "host": "127.0.0.1",
                        "port": 15021,
                        "byte_order": "big",
                        "word_order": "msw_first",
                        "points": {"v_poi_write": dict(point, address=401)},
                    },
                    "remote": {
                        "host": "10.0.0.22",
                        "port": 503,
                        "byte_order": "big",
                        "word_order": "msw_first",
                        "points": {"v_poi_write": dict(point, address=501)},
                    },
                }
            },
        },
    }


def _grid_map_background_config(background_mode="street"):
    return {"GRID_MAP_BACKGROUND_MODE": background_mode}


class GridMapRuntimeTests(unittest.TestCase):
    def setUp(self):
        gmr._SIMULATOR_MODULE = None
        self.config = {"TIMEZONE_NAME": "Europe/Madrid"}

    def tearDown(self):
        gmr._SIMULATOR_MODULE = None

    def test_voltage_color_uses_new_discrete_buckets(self):
        self.assertEqual(gmr._voltage_color(None), gmr.GRID_MAP_VOLTAGE_COLOR_MISSING)
        self.assertEqual(gmr._voltage_color(0.9249), gmr.GRID_MAP_VOLTAGE_COLOR_RED)
        self.assertEqual(gmr._voltage_color(0.925), gmr.GRID_MAP_VOLTAGE_COLOR_AMBER)
        self.assertEqual(gmr._voltage_color(0.95), gmr.GRID_MAP_VOLTAGE_COLOR_YELLOW_GREEN)
        self.assertEqual(gmr._voltage_color(0.975), gmr.GRID_MAP_VOLTAGE_COLOR_GREEN)
        self.assertEqual(gmr._voltage_color(1.025), gmr.GRID_MAP_VOLTAGE_COLOR_DARK_CYAN_GREEN)
        self.assertEqual(gmr._voltage_color(1.05), gmr.GRID_MAP_VOLTAGE_COLOR_LIGHT_BLUE_GREEN)
        self.assertEqual(gmr._voltage_color(1.075), gmr.GRID_MAP_VOLTAGE_COLOR_BLUE)

    def test_voltage_color_palette_matches_contrast_friendly_progression(self):
        self.assertEqual(gmr.GRID_MAP_VOLTAGE_COLOR_YELLOW_GREEN, "#d7b62a")
        self.assertEqual(gmr.GRID_MAP_VOLTAGE_COLOR_GREEN, "#96cc56")
        self.assertEqual(gmr.GRID_MAP_VOLTAGE_COLOR_DARK_CYAN_GREEN, "#2e8f85")
        self.assertEqual(gmr.GRID_MAP_VOLTAGE_COLOR_LIGHT_BLUE_GREEN, "#5d97c9")

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

    def test_run_grid_map_power_flow_inverts_active_power_and_derives_absolute_voltage_when_missing(self):
        fake_simulator = _FakeSimulatorModule(include_voltage_kv=False)
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
        self.assertEqual(call["battery_q_mvar"], 0.05)
        self.assertIn("+02:00", call["timestamp_iso"])
        self.assertEqual(result["battery_input_p_mw"], -0.25)
        self.assertEqual(result["battery_input_q_mvar"], 0.05)
        self.assertEqual(result["power_flow_result"]["battery_bus_vm_kv"], 20.4)

    def test_write_grid_map_optional_voltage_point_skips_when_voltage_unavailable(self):
        config = _grid_map_write_config()
        shared_data = {"lock": threading.Lock(), "transport_mode": "local"}

        with patch.object(gmr, "ModbusClient") as client_cls:
            result = gmr.write_grid_map_optional_voltage_point(
                config,
                shared_data,
                {"power_flow_result": {}},
            )

        self.assertEqual(result["state"], "skipped")
        self.assertEqual(result["message"], "voltage_unavailable")
        self.assertEqual(result["targets"], [])
        client_cls.assert_not_called()

    def test_write_grid_map_optional_voltage_point_skips_when_not_configured(self):
        config = _grid_map_write_config()
        config["PLANTS"]["lib"]["modbus"]["local"]["points"] = {}
        config["PLANTS"]["vrfb"]["modbus"]["local"]["points"] = {}
        shared_data = {"lock": threading.Lock(), "transport_mode": "local"}

        with patch.object(gmr, "ModbusClient") as client_cls:
            result = gmr.write_grid_map_optional_voltage_point(
                config,
                shared_data,
                {"power_flow_result": {"battery_bus_vm_kv": 0.42}},
            )

        self.assertEqual(result["state"], "skipped")
        self.assertEqual(result["message"], "point_not_configured")
        self.assertEqual(result["targets"], [])
        client_cls.assert_not_called()

    def test_write_grid_map_optional_voltage_point_writes_to_all_active_local_endpoints(self):
        config = _grid_map_write_config()
        shared_data = {"lock": threading.Lock(), "transport_mode": "local"}
        client_events = []
        write_calls = []

        class _FakeClient:
            def __init__(self, host, port):
                self.host = host
                self.port = port
                client_events.append(("init", host, port))

            def open(self):
                client_events.append(("open", self.host, self.port))
                return True

            def close(self):
                client_events.append(("close", self.host, self.port))

        def _fake_write(client, endpoint, point_name, value):
            write_calls.append(
                {
                    "host": client.host,
                    "port": client.port,
                    "endpoint_host": endpoint.get("host"),
                    "endpoint_port": endpoint.get("port"),
                    "point_name": point_name,
                    "value": value,
                }
            )
            return True

        with patch.object(gmr, "ModbusClient", _FakeClient), patch.object(
            gmr, "write_point_internal", side_effect=_fake_write
        ):
            result = gmr.write_grid_map_optional_voltage_point(
                config,
                shared_data,
                {"power_flow_result": {"battery_bus_vm_kv": 0.42}},
            )

        self.assertEqual(result["state"], "ok")
        self.assertEqual(len(write_calls), 2)
        self.assertEqual(
            {(call["host"], call["port"]) for call in write_calls},
            {("127.0.0.1", 15020), ("127.0.0.1", 15021)},
        )
        self.assertTrue(all(call["point_name"] == "v_poi_write" for call in write_calls))
        self.assertTrue(all(abs(call["value"] - 0.42) < 1e-6 for call in write_calls))
        self.assertEqual(client_events[0], ("init", "127.0.0.1", 15020))

    def test_write_grid_map_optional_voltage_point_skips_plants_without_configured_point(self):
        config = _grid_map_write_config()
        config["PLANTS"]["lib"]["modbus"]["local"]["points"] = {}
        shared_data = {"lock": threading.Lock(), "transport_mode": "local"}
        write_calls = []

        class _FakeClient:
            def __init__(self, host, port):
                self.host = host
                self.port = port

            def open(self):
                return True

            def close(self):
                return None

        def _fake_write(client, endpoint, point_name, value):
            write_calls.append((client.host, client.port, endpoint.get("host"), endpoint.get("port"), point_name, value))
            return True

        with patch.object(gmr, "ModbusClient", _FakeClient), patch.object(
            gmr, "write_point_internal", side_effect=_fake_write
        ):
            result = gmr.write_grid_map_optional_voltage_point(
                config,
                shared_data,
                {"power_flow_result": {"battery_bus_vm_kv": 0.42}},
            )

        self.assertEqual(result["state"], "ok")
        self.assertEqual(len(write_calls), 1)
        self.assertEqual(write_calls[0][0:2], ("127.0.0.1", 15021))
        self.assertEqual(write_calls[0][4], "v_poi_write")
        self.assertAlmostEqual(write_calls[0][5], 0.42, places=6)

    def test_write_grid_map_optional_voltage_point_uses_active_remote_transport(self):
        config = _grid_map_write_config()
        shared_data = {"lock": threading.Lock(), "transport_mode": "remote"}
        write_calls = []

        class _FakeClient:
            def __init__(self, host, port):
                self.host = host
                self.port = port

            def open(self):
                return True

            def close(self):
                return None

        def _fake_write(client, endpoint, point_name, value):
            write_calls.append(
                {
                    "host": client.host,
                    "port": client.port,
                    "endpoint_host": endpoint.get("host"),
                    "endpoint_port": endpoint.get("port"),
                    "point_name": point_name,
                    "value": value,
                }
            )
            return True

        with patch.object(gmr, "ModbusClient", _FakeClient), patch.object(
            gmr, "write_point_internal", side_effect=_fake_write
        ):
            result = gmr.write_grid_map_optional_voltage_point(
                config,
                shared_data,
                {"power_flow_result": {"battery_bus_vm_kv": 19.8}},
            )

        self.assertEqual(result["state"], "ok")
        self.assertEqual(len(write_calls), 2)
        self.assertEqual(
            {(call["host"], call["port"]) for call in write_calls},
            {("10.0.0.21", 502), ("10.0.0.22", 503)},
        )
        self.assertTrue(all(call["point_name"] == "v_poi_write" for call in write_calls))
        self.assertTrue(all(abs(call["value"] - 19.8) < 1e-6 for call in write_calls))

    def test_write_grid_map_optional_voltage_point_failure_is_nonfatal_to_runtime_state(self):
        config = _grid_map_write_config()
        shared_data = {"lock": threading.Lock(), "transport_mode": "local"}

        class _FakeClient:
            def __init__(self, host, port):
                self.host = host
                self.port = port

            def open(self):
                return True

            def close(self):
                return None

        with patch.object(gmr, "ModbusClient", _FakeClient), patch.object(
            gmr, "write_point_internal", return_value=False
        ):
            result = gmr.write_grid_map_optional_voltage_point(
                config,
                shared_data,
                {"power_flow_result": {"battery_bus_vm_kv": 0.42}},
            )

        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["message"], "write_failed")
        self.assertEqual(len(result["targets"]), 2)

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
        runtime_state = gmr.snapshot_grid_map_runtime(shared_data)

        self.assertEqual(runtime_state["state"], "ok")
        self.assertIsNone(runtime_state["last_error"])

    def test_build_power_flow_summary_and_dynamic_payload_extract_expected_values(self):
        topology_cache = {
            "bus_order": [1, 2, 3],
            "line_order": [11, 12],
            "trafo_order": [21],
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
                "res_trafo": pd.DataFrame({"loading_percent": [85.0]}, index=[21]),
            },
        }

        summary = gmr.build_power_flow_summary(result)
        dynamic = gmr.build_dynamic_payload(result, topology_cache)

        self.assertEqual(summary["num_voltage_violations"], 1)
        self.assertEqual(summary["num_overloaded_lines"], 1)
        self.assertEqual(dynamic["bus"]["3"]["status"], "violation")
        self.assertEqual(dynamic["line"]["12"]["status"], "overloaded")
        self.assertEqual(dynamic["trafo"]["21"]["status"], "ok")

    def test_build_dynamic_payload_keeps_operational_voltage_violation_limits(self):
        topology_cache = {
            "bus_order": [1, 2, 3, 4],
            "line_order": [],
            "trafo_order": [],
        }
        result = {
            "results_tables": {
                "res_bus": pd.DataFrame({"vm_pu": [0.94, 0.96, 1.04, 1.06]}, index=[1, 2, 3, 4]),
            },
        }

        dynamic = gmr.build_dynamic_payload(result, topology_cache)

        self.assertEqual(dynamic["bus"]["1"]["status"], "violation")
        self.assertEqual(dynamic["bus"]["2"]["status"], "ok")
        self.assertEqual(dynamic["bus"]["3"]["status"], "ok")
        self.assertEqual(dynamic["bus"]["4"]["status"], "violation")

    def test_publish_grid_map_error_preserves_last_successful_payload(self):
        shared_data = {"lock": threading.Lock(), gmr.GRID_MAP_STATUS_KEY: gmr.default_grid_map_runtime(5.0)}
        shared_data[gmr.GRID_MAP_STATUS_KEY]["topology_cache"] = {"bus_order": [1], "line_order": [], "trafo_order": []}
        shared_data[gmr.GRID_MAP_STATUS_KEY]["trace_index_meta"] = [{"index": 0, "role": "bus", "element_index": None}]
        shared_data[gmr.GRID_MAP_STATUS_KEY]["topology_revision"] = 1234
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
        fake_plotly.create_bus_trace = _fake_bus_trace
        fake_plotly.create_line_trace = _fake_line_trace
        fake_plotly.create_trafo_trace = _fake_trafo_trace
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
        self.assertTrue(bool(topology["trace_index_meta"]))
        self.assertTrue(bool(topology["topology_revision"]))
        self.assertEqual(topology["coordinate_mode"], "schematic")

    def test_build_topology_cache_uses_assets_when_simulator_helpers_are_absent(self):
        fake_simulator = _FakeAssetsOnlySimulatorModule()
        gmr._SIMULATOR_MODULE = fake_simulator

        fake_plotting = types.ModuleType("pandapower.plotting")
        fake_plotting.create_generic_coordinates = _fake_create_generic_coordinates

        with patch.dict(sys.modules, {"pandapower.plotting": fake_plotting}):
            topology = gmr.build_topology_cache()

        self.assertGreaterEqual(fake_simulator.asset_loads, 1)
        self.assertEqual(topology["metadata"], {"battery_bus": 2, "hub_bus": 1})
        self.assertEqual(topology["bus_order"], [1, 2, 3])
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
            topology = gmr.build_topology_cache(_grid_map_background_config())

        self.assertEqual(topology["coordinate_mode"], "geographic")
        self.assertEqual(topology["source_crs"], gmr.GRID_MAP_SOURCE_CRS)
        self.assertEqual(topology["target_crs"], gmr.GRID_MAP_TARGET_CRS)
        self.assertEqual(topology["map_background_mode"], "street")
        self.assertTrue(topology["map_background_enabled"])
        self.assertIn("1", topology["geographic_bus_coords"])
        self.assertIn("11", topology["geographic_line_paths"])
        self.assertIn("21", topology["geographic_trafo_paths"])

    def test_build_topology_cache_keeps_geographic_mode_without_tiles_when_background_is_none(self):
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
            topology = gmr.build_topology_cache(_grid_map_background_config("none"))

        self.assertEqual(topology["coordinate_mode"], "geographic")
        self.assertEqual(topology["map_background_mode"], "none")
        self.assertFalse(topology["map_background_enabled"])
        self.assertIsNone(topology["map_background_reason"])
        self.assertEqual(topology["initial_figure"]["layout"]["map"]["style"], gmr.GRID_MAP_BACKGROUND_STYLE_BY_MODE["none"])

    def test_normalize_geojson_components_for_convert_crs_clears_nullable_geo_columns(self):
        net = _FakeNet(with_projected_geo=True)
        net.line.loc[11, "geo"] = '{"coordinates":[[482068.167,4071882.831],[482063.167,4071891.83]], "type":"LineString"}'
        net.line.loc[12, "geo"] = None

        gmr._normalize_geojson_components_for_convert_crs(net)

        self.assertTrue(isinstance(net.bus_geodata, pd.DataFrame) and not net.bus_geodata.empty)
        self.assertTrue(isinstance(net.line_geodata, pd.DataFrame) and not net.line_geodata.empty)
        self.assertTrue(net.bus["geo"].isna().all())
        self.assertTrue(net.line["geo"].isna().all())

    def test_line_center_returns_arithmetic_midpoint_for_two_point_path(self):
        center = gmr._line_center([(0.0, 0.0), (4.0, 2.0)])

        self.assertEqual(center, (2.0, 1.0))

    def test_line_center_returns_path_length_midpoint_for_polyline(self):
        center = gmr._line_center([(0.0, 0.0), (3.0, 0.0), (3.0, 1.0)])

        self.assertEqual(center, (2.0, 0.0))

    def test_prepare_plot_net_for_pandapower_traces_normalizes_geo_columns(self):
        plot_net = gmr._prepare_plot_net(
            _FakeNet(with_projected_geo=True),
            bus_coords={1: (1.0, 1.0), 2: (2.0, 2.0), 3: (3.0, 3.0)},
            line_paths={11: [(1.0, 1.0), (2.0, 2.0)], 12: [(2.0, 2.0), (3.0, 3.0)]},
        )

        prepared = gmr._prepare_plot_net_for_pandapower_traces(plot_net)

        self.assertTrue(prepared.bus["geo"].isna().all())
        self.assertTrue(prepared.line["geo"].isna().all())
        self.assertTrue(isinstance(prepared.bus_geodata, pd.DataFrame) and not prepared.bus_geodata.empty)
        self.assertTrue(isinstance(prepared.line_geodata, pd.DataFrame) and not prepared.line_geodata.empty)

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
            topology = gmr.build_topology_cache(_grid_map_background_config("satellite"))

        self.assertEqual(topology["coordinate_mode"], "schematic")
        self.assertEqual(topology["map_background_mode"], "none")
        self.assertFalse(topology["map_background_enabled"])
        self.assertIn("coordinate_conversion_failed", str(topology["map_background_reason"]))

    def test_build_topology_cache_uses_true_midpoints_for_line_hover_points(self):
        fake_simulator = _FakeSimulatorModule()
        gmr._SIMULATOR_MODULE = fake_simulator

        fake_plotting = types.ModuleType("pandapower.plotting")
        fake_plotting.create_generic_coordinates = _fake_create_generic_coordinates

        with patch.dict(sys.modules, {"pandapower.plotting": fake_plotting}):
            topology = gmr.build_topology_cache()

        self.assertEqual(topology["line_center_points"]["11"], [0.5, 0.25])
        self.assertEqual(topology["line_center_points"]["12"], [1.5, 0.75])
        self.assertNotEqual(topology["line_center_points"]["11"], [1.0, 0.5])

    def test_build_grid_map_figure_reuses_topology_structure(self):
        topology_cache = {
            "coordinate_mode": "schematic",
            "map_background_mode": "none",
            "map_background_enabled": False,
            "bus_order": [1, 2, 3],
            "line_order": [11, 12],
            "trafo_order": [21],
            "plot_net": _FakeNet(),
            "topology_revision": 1001,
            "initial_figure": {"data": [{"name": "placeholder"}], "layout": {"meta": {}}},
        }
        payload_a = {
            "bus": {"1": {"vm_pu": 0.98}, "2": {"vm_pu": 1.0}, "3": {"vm_pu": 1.02}},
            "line": {"11": {"loading_pct": 40.0}, "12": {"loading_pct": 90.0}},
        }
        payload_b = {
            "bus": {"1": {"vm_pu": 0.94}, "2": {"vm_pu": 1.0}, "3": {"vm_pu": 1.06}},
            "line": {"11": {"loading_pct": 40.0}, "12": {"loading_pct": 120.0}},
        }

        fake_plotly = types.ModuleType("pandapower.plotting.plotly")
        fake_plotly.create_bus_trace = _fake_bus_trace
        fake_plotly.create_line_trace = _fake_line_trace
        fake_plotly.create_trafo_trace = _fake_trafo_trace
        fake_plotly.draw_traces = _fake_draw_traces

        with patch.dict(sys.modules, {"pandapower.plotting.plotly": fake_plotly}):
            fig_a = gmr.build_grid_map_figure(topology_cache, payload_a, uirevision_key="same-key")
            fig_b = gmr.build_grid_map_figure(topology_cache, payload_b, uirevision_key="same-key")

        self.assertEqual(len(fig_a.data), len(fig_b.data))
        self.assertEqual(fig_a.layout.uirevision, "same-key")
        self.assertEqual(fig_b.layout.uirevision, "same-key")
        self.assertEqual(fig_a.layout.meta["grid_map_topology_revision"], 1001)
        self.assertEqual(
            list(fig_b.data[-1].marker.color),
            [
                gmr.GRID_MAP_VOLTAGE_COLOR_AMBER,
                gmr.GRID_MAP_VOLTAGE_COLOR_GREEN,
                gmr.GRID_MAP_VOLTAGE_COLOR_LIGHT_BLUE_GREEN,
            ],
        )

    def test_build_grid_map_figure_uses_geographic_background_style_when_available(self):
        topology_cache = {
            "coordinate_mode": "geographic",
            "map_background_mode": "street",
            "map_background_enabled": True,
            "geographic_bounds": {"x_min": -5.18, "x_max": -5.17, "y_min": 40.71, "y_max": 40.72},
            "geographic_center": {"lon": -5.175, "lat": 40.715},
            "bus_order": [1, 2, 3],
            "line_order": [11, 12],
            "trafo_order": [21],
            "plot_net": _FakeNet(with_projected_geo=True),
            "topology_revision": 2002,
            "initial_figure": {"data": [{"name": "placeholder"}], "layout": {"meta": {}}},
        }
        payload = {
            "bus": {"1": {"vm_pu": 0.98}, "2": {"vm_pu": 1.0}, "3": {"vm_pu": 1.02}},
            "line": {"11": {"loading_pct": 40.0}, "12": {"loading_pct": 90.0}},
        }

        fake_plotly = types.ModuleType("pandapower.plotting.plotly")
        fake_plotly.create_bus_trace = _fake_bus_trace
        fake_plotly.create_line_trace = _fake_line_trace
        fake_plotly.create_trafo_trace = _fake_trafo_trace
        fake_plotly.draw_traces = _fake_draw_traces

        with patch.dict(sys.modules, {"pandapower.plotting.plotly": fake_plotly}):
            fig = gmr.build_grid_map_figure(topology_cache, payload, uirevision_key="geo-key")

        self.assertEqual(fig.layout.uirevision, "geo-key")
        self.assertFalse(bool(getattr(fig.layout.title, "text", None)))
        self.assertEqual(fig.layout.map.style, gmr.GRID_MAP_BACKGROUND_STYLE_BY_MODE["street"])
        self.assertEqual(fig.layout.map.center.lon, -5.175)
        self.assertEqual(fig.layout.map.center.lat, 40.715)
        self.assertGreater(len(fig.data), 0)

    def test_build_grid_map_figure_uses_white_bg_for_none_mode_on_geographic_render(self):
        topology_cache = {
            "coordinate_mode": "geographic",
            "map_background_mode": "none",
            "map_background_enabled": False,
            "geographic_bounds": {"x_min": -5.18, "x_max": -5.17, "y_min": 40.71, "y_max": 40.72},
            "geographic_center": {"lon": -5.175, "lat": 40.715},
            "bus_order": [1, 2, 3],
            "line_order": [11, 12],
            "trafo_order": [21],
            "plot_net": _FakeNet(with_projected_geo=True),
            "topology_revision": 2003,
            "initial_figure": {"data": [{"name": "placeholder"}], "layout": {"meta": {}}},
        }
        payload = {
            "bus": {"1": {"vm_pu": 0.98}, "2": {"vm_pu": 1.0}, "3": {"vm_pu": 1.02}},
            "line": {"11": {"loading_pct": 40.0}, "12": {"loading_pct": 90.0}},
        }

        fake_plotly = types.ModuleType("pandapower.plotting.plotly")
        fake_plotly.create_bus_trace = _fake_bus_trace
        fake_plotly.create_line_trace = _fake_line_trace
        fake_plotly.create_trafo_trace = _fake_trafo_trace
        fake_plotly.draw_traces = _fake_draw_traces

        with patch.dict(sys.modules, {"pandapower.plotting.plotly": fake_plotly}):
            fig = gmr.build_grid_map_figure(topology_cache, payload, uirevision_key="geo-none-key")

        self.assertEqual(fig.layout.map.style, gmr.GRID_MAP_BACKGROUND_STYLE_BY_MODE["none"])
        self.assertGreater(len(fig.data), 0)

    def test_build_grid_map_figure_uses_satellite_style_when_requested(self):
        topology_cache = {
            "coordinate_mode": "geographic",
            "map_background_mode": "satellite",
            "map_background_enabled": True,
            "geographic_bounds": {"x_min": -5.18, "x_max": -5.17, "y_min": 40.71, "y_max": 40.72},
            "geographic_center": {"lon": -5.175, "lat": 40.715},
            "bus_order": [1, 2, 3],
            "line_order": [11, 12],
            "trafo_order": [21],
            "plot_net": _FakeNet(with_projected_geo=True),
            "topology_revision": 2004,
            "initial_figure": {"data": [{"name": "placeholder"}], "layout": {"meta": {}}},
        }
        payload = {
            "bus": {"1": {"vm_pu": 0.98}, "2": {"vm_pu": 1.0}, "3": {"vm_pu": 1.02}},
            "line": {"11": {"loading_pct": 40.0}, "12": {"loading_pct": 90.0}},
        }

        fake_plotly = types.ModuleType("pandapower.plotting.plotly")
        fake_plotly.create_bus_trace = _fake_bus_trace
        fake_plotly.create_line_trace = _fake_line_trace
        fake_plotly.create_trafo_trace = _fake_trafo_trace
        fake_plotly.draw_traces = _fake_draw_traces

        with patch.dict(sys.modules, {"pandapower.plotting.plotly": fake_plotly}):
            fig = gmr.build_grid_map_figure(topology_cache, payload, uirevision_key="geo-satellite-key")

        self.assertEqual(fig.layout.map.style, gmr.GRID_MAP_BACKGROUND_STYLE_BY_MODE["satellite"])
        self.assertGreater(len(fig.data), 0)

    def test_build_grid_map_meta_lines_include_effective_background_mode(self):
        lines = gmr.build_grid_map_meta_lines(
            {
                "last_success_at": "2026-04-03T12:00:00+02:00",
                "input_source": "observed_state",
                "stale": False,
                "coordinate_mode": "geographic",
                "source_crs": gmr.GRID_MAP_SOURCE_CRS,
                "target_crs": gmr.GRID_MAP_TARGET_CRS,
                "map_background_mode": "satellite",
                "map_background_enabled": True,
                "requested_timestamp_local": "2026-04-03T12:00:00+02:00",
                "selected_timestamp_local": "2026-04-03T12:00:00+02:00",
                "used_previous_hour_fallback": False,
                "battery_input_p_kw": 10.0,
                "battery_input_q_kvar": 2.0,
            },
            {"TIMEZONE_NAME": "Europe/Madrid"},
        )

        self.assertIn("Background: satellite", lines[1])
        self.assertIn("Tiles Enabled: True", lines[1])

    def test_build_grid_map_figure_update_reuses_prerendered_initial_figure_on_first_load(self):
        initial_figure = {
            "data": [
                {"name": "Lines", "mode": "lines", "line": {"color": "#0000ff"}, "text": "Line 11"},
                {"name": "edge_center", "mode": "markers", "text": ["Line 11"]},
                {"name": "Transformers", "mode": "lines", "line": {"color": "#008000"}, "text": "Transformer 21"},
                {"name": "edge_center", "mode": "markers", "text": ["Transformer 21"]},
                {"name": "Buses", "mode": "markers", "marker": {"color": ["#0000ff", "#0000ff"]}, "text": ["Bus 1", "Bus 2"]},
            ],
            "layout": {"meta": {"grid_map_topology_revision": 0, "grid_map_dynamic_revision": 0}},
        }
        runtime_state = {
            "topology_cache": {"bus_order": [1, 2], "line_order": [11], "trafo_order": [21]},
            "initial_figure": initial_figure,
            "trace_index_meta": [
                {"index": 0, "role": "line", "element_index": 11},
                {"index": 1, "role": "line_hover", "element_index": None},
                {"index": 2, "role": "trafo", "element_index": 21},
                {"index": 3, "role": "trafo_hover", "element_index": None},
                {"index": 4, "role": "bus", "element_index": None},
            ],
            "topology_revision": 5005,
            "dynamic_revision": 3,
            "dynamic_payload": {
                "bus": {
                    "1": {"vm_pu": 0.94, "hover": "Bus 1<br>Voltage=0.9400 pu"},
                    "2": {"vm_pu": 1.01, "hover": "Bus 2<br>Voltage=1.0100 pu"},
                },
                "line": {"11": {"loading_pct": 120.0, "hover": "Line 11<br>Loading=120.0%"}},
                "trafo": {"21": {"loading_pct": 85.0, "hover": "Transformer 21<br>Loading=85.0%"}},
            },
        }

        figure = gmr.build_grid_map_figure_update(runtime_state, None, uirevision_key="cached-key")

        self.assertEqual(figure.layout.uirevision, "cached-key")
        self.assertEqual(figure.layout.meta["grid_map_topology_revision"], 5005)
        self.assertEqual(figure.layout.meta["grid_map_dynamic_revision"], 3)
        self.assertEqual(figure.data[0].line.color, "#d93838")
        self.assertEqual(figure.data[0].text, "Line 11<br>Loading=120.0%")
        self.assertEqual(list(figure.data[1].text), ["Line 11<br>Loading=120.0%"])
        self.assertEqual(figure.data[2].line.color, "#d28c00")
        self.assertEqual(list(figure.data[3].text), ["Transformer 21<br>Loading=85.0%"])
        self.assertEqual(
            list(figure.data[4].marker.color),
            [gmr.GRID_MAP_VOLTAGE_COLOR_AMBER, gmr.GRID_MAP_VOLTAGE_COLOR_GREEN],
        )
        self.assertEqual(list(figure.data[4].text), ["Bus 1<br>Voltage=0.9400 pu", "Bus 2<br>Voltage=1.0100 pu"])

    def test_build_grid_map_figure_update_low_trace_first_render_keeps_grouped_lines(self):
        topology_cache = {
            "figure_renderer": "low-trace",
            "coordinate_mode": "schematic",
            "map_background_mode": "none",
            "map_background_enabled": False,
            "metadata": {"battery_bus": 2},
            "bounds": {"x_min": 0.0, "x_max": 2.0, "y_min": 0.0, "y_max": 1.0},
            "bus_order": [1, 2, 3],
            "bus_coords": {"1": [0.0, 0.0], "2": [1.0, 0.5], "3": [2.0, 1.0]},
            "line_order": [11, 12],
            "line_paths": {"11": [[0.0, 0.0], [1.0, 0.5]], "12": [[1.0, 0.5], [2.0, 1.0]]},
            "line_center_points": {"11": [0.5, 0.25], "12": [1.5, 0.75]},
            "line_hover_order": [11, 12],
            "trace_roles": {role: idx for idx, role in enumerate(gmr._trace_roles())},
            "trafo_order": [21],
            "trafo_paths": {"21": [[0.0, 0.0], [2.0, 1.0]]},
            "topology_revision": 6006,
        }
        runtime_state = {
            "topology_cache": topology_cache,
            "topology_revision": 6006,
            "dynamic_revision": 1,
            "dynamic_payload": {
                "bus": {"1": {"vm_pu": 0.99}, "2": {"vm_pu": 1.0}, "3": {"vm_pu": 1.01}},
                "line": {"11": {"loading_pct": 20.0}, "12": {"loading_pct": 40.0}},
                "trafo": {},
            },
        }

        figure = gmr.build_grid_map_figure_update(runtime_state, None, uirevision_key="low-trace-key")

        self.assertEqual(len(figure.data), 6)
        self.assertGreater(len(list(figure.data[0].x)), 0)
        self.assertGreater(len(list(figure.data[4].x)), 0)
        self.assertEqual(figure.data[4].hovertemplate, "%{text}<extra></extra>")
        self.assertEqual(figure.data[4].marker.size, gmr.GRID_MAP_LINE_HOVER_MARKER_SIZE)
        self.assertEqual(figure.data[4].marker.color, gmr.GRID_MAP_LINE_HOVER_MARKER_COLOR)
        self.assertEqual(list(figure.data[4].x), [0.5, 1.5])
        self.assertEqual(list(figure.data[4].y), [0.25, 0.75])
        self.assertEqual(list(figure.data[4].text), ["Line 11<br>Loading=n/a", "Line 12<br>Loading=n/a"])

    def test_build_grid_map_figure_update_returns_none_when_revision_is_unchanged(self):
        runtime_state = {
            "topology_cache": {"plot_net": _FakeNet()},
            "topology_revision": 4004,
            "dynamic_revision": 7,
            "dynamic_payload": {"bus": {}, "line": {}},
        }
        current_figure = {
            "data": [],
            "layout": {"meta": {"grid_map_topology_revision": 4004, "grid_map_dynamic_revision": 7}},
        }

        update = gmr.build_grid_map_figure_update(runtime_state, current_figure, uirevision_key="same-key")

        self.assertIsNone(update)

    def test_build_grid_map_figure_update_low_trace_returns_new_figure_when_dynamic_revision_changes(self):
        topology_cache = {
            "figure_renderer": "low-trace",
            "coordinate_mode": "schematic",
            "map_background_mode": "none",
            "map_background_enabled": False,
            "metadata": {"battery_bus": 2},
            "bounds": {"x_min": 0.0, "x_max": 2.0, "y_min": 0.0, "y_max": 1.0},
            "bus_order": [1, 2, 3],
            "bus_coords": {"1": [0.0, 0.0], "2": [1.0, 0.5], "3": [2.0, 1.0]},
            "line_order": [11, 12],
            "line_paths": {"11": [[0.0, 0.0], [1.0, 0.5]], "12": [[1.0, 0.5], [2.0, 1.0]]},
            "line_center_points": {"11": [0.5, 0.25], "12": [1.5, 0.75]},
            "line_hover_order": [11, 12],
            "trace_roles": {role: idx for idx, role in enumerate(gmr._trace_roles())},
            "trafo_order": [21],
            "trafo_paths": {"21": [[0.0, 0.0], [2.0, 1.0]]},
            "topology_revision": 7007,
        }
        runtime_state = {
            "topology_cache": topology_cache,
            "topology_revision": 7007,
            "dynamic_revision": 2,
            "dynamic_payload": {
                "bus": {
                    "1": {"vm_pu": 0.94, "hover": "Bus 1<br>Voltage=0.9400 pu"},
                    "2": {"vm_pu": 1.00, "hover": "Bus 2<br>Voltage=1.0000 pu"},
                    "3": {"vm_pu": 1.06, "hover": "Bus 3<br>Voltage=1.0600 pu"},
                },
                "line": {
                    "11": {"loading_pct": 20.0, "hover": "Line 11<br>Loading=20.0%"},
                    "12": {"loading_pct": 120.0, "hover": "Line 12<br>Loading=120.0%"},
                },
                "trafo": {},
            },
        }
        current_figure = {"layout": {"meta": {"grid_map_topology_revision": 7007, "grid_map_dynamic_revision": 1}}}

        figure = gmr.build_grid_map_figure_update(runtime_state, current_figure, uirevision_key="refresh-key")

        self.assertIsNotNone(figure)
        self.assertEqual(figure.layout.uirevision, "refresh-key")
        self.assertEqual(figure.layout.meta["grid_map_dynamic_revision"], 2)
        self.assertEqual(len(figure.data), 6)
        self.assertEqual(list(figure.data[4].text), ["Line 11<br>Loading=20.0%", "Line 12<br>Loading=120.0%"])
        self.assertEqual(list(figure.data[5].text), ["Bus 1<br>Voltage=0.9400 pu", "Bus 2<br>Voltage=1.0000 pu", "Bus 3<br>Voltage=1.0600 pu"])



if __name__ == "__main__":
    unittest.main()
