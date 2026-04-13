import queue
import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from control.command_runtime import enqueue_control_command
from control.engine_agent import (
    _execute_command,
    _publish_observed_state,
    _run_single_engine_cycle,
    _safe_stop_plant,
    _start_one_plant,
    _stop_one_plant,
)
from modbus.codec import read_point_internal


class _MemoryModbusClient:
    def __init__(self, host, port):
        self.host = str(host)
        self.port = int(port)
        self.is_open = False
        self.registers = {}

    def open(self):
        self.is_open = True
        return True

    def close(self):
        self.is_open = False

    def write_single_register(self, address, value):
        self.registers[int(address)] = int(value)
        return True

    def read_holding_registers(self, address, count):
        return [self.registers.get(int(address) + idx, 0) for idx in range(int(count))]


def _shared_data():
    return {
        "lock": threading.Lock(),
        "scheduler_running_by_plant": {"lib": False, "vrfb": False},
        "plant_transition_by_plant": {"lib": "stopped", "vrfb": "stopped"},
        "measurements_filename_by_plant": {"lib": "data/lib.csv", "vrfb": "data/vrfb.csv"},
        "current_file_path_by_plant": {"lib": None, "vrfb": None},
        "current_file_df_by_plant": {"lib": None, "vrfb": None},
        "transport_mode": "remote",
        "transport_switching": False,
        "local_emulator_soc_seed_request_by_plant": {"lib": None, "vrfb": None},
        "local_emulator_soc_seed_result_by_plant": {
            "lib": {"request_id": None, "status": "idle", "soc_pu": None, "message": None},
            "vrfb": {"request_id": None, "status": "idle", "soc_pu": None, "message": None},
        },
        "control_command_queue": queue.Queue(maxsize=16),
        "control_command_status_by_id": {},
        "control_command_history_ids": [],
        "control_command_active_id": None,
        "control_command_next_id": 1,
        "plant_observed_state_by_plant": {
            "lib": {
                "enable_state": None,
                "start_command_state": None,
                "stop_command_state": None,
                "p_battery_kw": None,
                "q_battery_kvar": None,
                "last_attempt": None,
                "last_success": None,
                "error": None,
                "read_status": "unknown",
                "last_error": None,
                "consecutive_failures": 0,
                "stale": True,
            },
            "vrfb": {
                "enable_state": None,
                "start_command_state": None,
                "stop_command_state": None,
                "p_battery_kw": None,
                "q_battery_kvar": None,
                "last_attempt": None,
                "last_success": None,
                "error": None,
                "read_status": "unknown",
                "last_error": None,
                "consecutive_failures": 0,
                "stale": True,
            },
        },
        "plant_operating_state_by_plant": {"lib": "unknown", "vrfb": "unknown"},
        "dispatch_write_status_by_plant": {
            "lib": {"sending_enabled": False},
            "vrfb": {"sending_enabled": False},
        },
        "control_engine_status": {
            "alive": False,
            "last_loop_start": None,
            "last_loop_end": None,
            "last_observed_refresh": None,
            "last_exception": None,
            "active_command_id": None,
            "active_command_kind": None,
            "active_command_started_at": None,
            "last_finished_command": None,
            "queue_depth": 0,
            "queued_count": 0,
            "running_count": 0,
            "failed_recent_count": 0,
        },
    }


class ControlEngineAgentTests(unittest.TestCase):
    @patch("control.modbus_io.ModbusClient")
    def test_safe_stop_zero_writes_use_per_phase_endpoint_when_configured(self, client_cls):
        client = _MemoryModbusClient("127.0.0.1", 502)
        client_cls.return_value = client
        shared_data = _shared_data()
        shared_data["scheduler_running_by_plant"]["lib"] = True
        endpoint_cfg = {
            "host": "127.0.0.1",
            "port": 502,
            "mode": "remote",
            "byte_order": "big",
            "word_order": "msw_first",
            "points": {
                "p_u_setpoint": {"name": "p_u_setpoint", "address": 10, "format": "int16", "word_count": 1, "unit": "kW", "eng_per_count": 0.1},
                "p_v_setpoint": {"name": "p_v_setpoint", "address": 11, "format": "int16", "word_count": 1, "unit": "kW", "eng_per_count": 0.1},
                "p_w_setpoint": {"name": "p_w_setpoint", "address": 12, "format": "int16", "word_count": 1, "unit": "kW", "eng_per_count": 0.1},
                "q_u_setpoint": {"name": "q_u_setpoint", "address": 20, "format": "int16", "word_count": 1, "unit": "kvar", "eng_per_count": 0.1},
                "q_v_setpoint": {"name": "q_v_setpoint", "address": 21, "format": "int16", "word_count": 1, "unit": "kvar", "eng_per_count": 0.1},
                "q_w_setpoint": {"name": "q_w_setpoint", "address": 22, "format": "int16", "word_count": 1, "unit": "kvar", "eng_per_count": 0.1},
            },
        }

        with patch("control.engine_agent._get_plant_modbus_config", return_value=endpoint_cfg), patch(
            "control.engine_agent._wait_until_battery_power_below_threshold", return_value=True
        ), patch("control.engine_agent._set_enable", return_value=True), patch(
            "control.engine_agent._run_stop_command_sequence", return_value={"ok": True, "details": []}
        ):
            result = _safe_stop_plant({"PLANT_IDS": ("lib", "vrfb")}, shared_data, "lib")

        self.assertTrue(result["threshold_reached"])
        self.assertTrue(result["disable_ok"])
        for point_name in (
            "p_u_setpoint",
            "p_v_setpoint",
            "p_w_setpoint",
            "q_u_setpoint",
            "q_v_setpoint",
            "q_w_setpoint",
        ):
            self.assertAlmostEqual(read_point_internal(client, endpoint_cfg, point_name), 0.0, places=6)

    def test_start_one_plant_success_preserves_dispatch_gate_and_updates_transition(self):
        shared_data = _shared_data()
        calls = []
        shared_data["scheduler_running_by_plant"]["lib"] = False

        result = _start_one_plant(
            {"STARTUP_INITIAL_SOC_PU": 0.5},
            shared_data,
            "lib",
            tz=timezone.utc,
            set_enable_fn=lambda plant_id, value: calls.append(("enable", plant_id, value)) or True,
            send_setpoints_fn=lambda plant_id, p_kw, q_kvar: calls.append(("setpoints", plant_id, p_kw, q_kvar)) or True,
            get_latest_schedule_setpoint_fn=lambda plant_id: (12.5, -3.0),
        )

        self.assertEqual(result["state"], "succeeded")
        self.assertTrue(result["result"]["enable_ok"])
        self.assertFalse(result["result"]["initial_setpoint_write_ok"])
        self.assertTrue(result["result"]["initial_setpoint_write_skipped"])
        self.assertFalse(result["result"]["dispatch_enabled"])
        self.assertEqual(shared_data["scheduler_running_by_plant"]["lib"], False)
        self.assertEqual(shared_data["plant_transition_by_plant"]["lib"], "running")
        self.assertEqual(calls[0], ("enable", "lib", 1))
        self.assertEqual(len(calls), 1)

    def test_start_one_plant_sends_initial_setpoints_when_dispatch_enabled(self):
        shared_data = _shared_data()
        calls = []
        shared_data["scheduler_running_by_plant"]["lib"] = True

        result = _start_one_plant(
            {"STARTUP_INITIAL_SOC_PU": 0.5},
            shared_data,
            "lib",
            tz=timezone.utc,
            set_enable_fn=lambda plant_id, value: calls.append(("enable", plant_id, value)) or True,
            send_setpoints_fn=lambda plant_id, p_kw, q_kvar: calls.append(("setpoints", plant_id, p_kw, q_kvar)) or True,
            get_latest_schedule_setpoint_fn=lambda plant_id: (12.5, -3.0),
        )

        self.assertEqual(result["state"], "succeeded")
        self.assertTrue(result["result"]["dispatch_enabled"])
        self.assertTrue(result["result"]["initial_setpoint_write_ok"])
        self.assertFalse(result["result"]["initial_setpoint_write_skipped"])
        self.assertEqual(shared_data["scheduler_running_by_plant"]["lib"], True)
        self.assertEqual(calls[0], ("enable", "lib", 1))
        self.assertEqual(calls[1], ("setpoints", "lib", 12.5, -3.0))

    def test_start_one_plant_publishes_clamped_initial_setpoint_status(self):
        shared_data = _shared_data()
        calls = []
        shared_data["scheduler_running_by_plant"]["lib"] = True
        config = {
            "STARTUP_INITIAL_SOC_PU": 0.5,
            "PLANTS": {
                "lib": {
                    "model": {
                        "power_limits": {
                            "p_max_kw": 10.0,
                            "p_min_kw": -10.0,
                            "q_max_kvar": 2.0,
                            "q_min_kvar": -2.0,
                        }
                    }
                }
            },
        }

        result = _start_one_plant(
            config,
            shared_data,
            "lib",
            tz=timezone.utc,
            set_enable_fn=lambda plant_id, value: True,
            send_setpoints_fn=lambda plant_id, p_kw, q_kvar: calls.append(("setpoints", plant_id, p_kw, q_kvar)) or True,
            get_latest_schedule_setpoint_fn=lambda plant_id: (12.5, -3.0),
        )

        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(calls[0], ("setpoints", "lib", 12.5, -3.0))
        self.assertAlmostEqual(result["result"]["initial_p_kw"], 10.0, places=6)
        self.assertAlmostEqual(result["result"]["initial_q_kvar"], -2.0, places=6)
        dispatch_state = dict(shared_data["dispatch_write_status_by_plant"]["lib"])
        self.assertAlmostEqual(float(dispatch_state["last_attempt_p_kw"]), 10.0, places=6)
        self.assertAlmostEqual(float(dispatch_state["last_attempt_q_kvar"]), -2.0, places=6)
        scheduler_ctx = dict(dispatch_state.get("last_scheduler_context") or {})
        self.assertTrue(scheduler_ctx.get("any_clamped"))
        self.assertTrue(scheduler_ctx.get("p_clamped"))
        self.assertTrue(scheduler_ctx.get("q_clamped"))
        self.assertAlmostEqual(float(scheduler_ctx.get("requested_p_kw")), 12.5, places=6)
        self.assertAlmostEqual(float(scheduler_ctx.get("requested_q_kvar")), -3.0, places=6)
        self.assertAlmostEqual(float(scheduler_ctx.get("applied_p_kw")), 10.0, places=6)
        self.assertAlmostEqual(float(scheduler_ctx.get("applied_q_kvar")), -2.0, places=6)

    def test_start_one_plant_resets_trigger_before_prepare_enable_and_setpoints(self):
        shared_data = _shared_data()
        calls = []
        shared_data["scheduler_running_by_plant"]["lib"] = True

        result = _start_one_plant(
            {"STARTUP_INITIAL_SOC_PU": 0.5},
            shared_data,
            "lib",
            tz=timezone.utc,
            reset_trigger_fn=lambda plant_id: calls.append(("trigger_reset", plant_id)) or {"state": "ok"},
            prepare_start_commands_fn=lambda plant_id: calls.append(("prepare", plant_id)) or {"ok": True, "details": []},
            set_enable_fn=lambda plant_id, value: calls.append(("enable", plant_id, value)) or True,
            send_setpoints_fn=lambda plant_id, p_kw, q_kvar: calls.append(("setpoints", plant_id, p_kw, q_kvar)) or True,
            get_latest_schedule_setpoint_fn=lambda plant_id: (12.5, -3.0),
        )

        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(calls[0], ("trigger_reset", "lib"))
        self.assertEqual(calls[1], ("prepare", "lib"))
        self.assertEqual(calls[2], ("enable", "lib", 1))
        self.assertEqual(calls[3], ("setpoints", "lib", 12.5, -3.0))

    def test_start_one_plant_trigger_reset_failure_fails_before_prepare(self):
        shared_data = _shared_data()
        shared_data["scheduler_running_by_plant"]["lib"] = True
        prepare_calls = []

        result = _start_one_plant(
            {"STARTUP_INITIAL_SOC_PU": 0.5},
            shared_data,
            "lib",
            tz=timezone.utc,
            reset_trigger_fn=lambda plant_id: {"state": "failed", "point": "trigger", "value": 0, "message": "write_failed"},
            prepare_start_commands_fn=lambda plant_id: prepare_calls.append(plant_id) or {"ok": True, "details": []},
            set_enable_fn=lambda plant_id, value: True,
            send_setpoints_fn=lambda plant_id, p_kw, q_kvar: True,
            get_latest_schedule_setpoint_fn=lambda plant_id: (1.0, 2.0),
        )

        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["message"], "command_prepare_failed")
        self.assertFalse(result["result"]["command_prepare_ok"])
        self.assertEqual(result["result"]["command_prepare_detail"][0]["point"], "trigger")
        self.assertEqual(shared_data["plant_transition_by_plant"]["lib"], "stopped")
        self.assertEqual(prepare_calls, [])

    @patch("modbus.setpoint_io._sleep")
    @patch("control.modbus_io.ModbusClient")
    def test_start_one_plant_uses_trigger_reset_and_triggered_initial_setpoint_write(self, client_cls, sleep_mock):
        client = _MemoryModbusClient("127.0.0.1", 502)
        client_cls.return_value = client
        shared_data = _shared_data()
        shared_data["scheduler_running_by_plant"]["lib"] = True
        endpoint_cfg = {
            "host": "127.0.0.1",
            "port": 502,
            "mode": "remote",
            "byte_order": "big",
            "word_order": "msw_first",
            "points": {
                "p_setpoint": {"name": "p_setpoint", "address": 10, "format": "int16", "word_count": 1, "unit": "kW", "eng_per_count": 0.1},
                "q_setpoint": {"name": "q_setpoint", "address": 11, "format": "int16", "word_count": 1, "unit": "kvar", "eng_per_count": 0.1},
                "trigger": {"name": "trigger", "address": 12, "format": "uint16", "word_count": 1, "unit": "raw", "eng_per_count": 1.0},
            },
        }

        with patch("control.engine_agent._get_plant_modbus_config", return_value=endpoint_cfg):
            result = _start_one_plant(
                {"STARTUP_INITIAL_SOC_PU": 0.5},
                shared_data,
                "lib",
                tz=timezone.utc,
                set_enable_fn=lambda plant_id, value: True,
                get_latest_schedule_setpoint_fn=lambda plant_id: (12.5, -3.0),
                prepare_start_commands_fn=lambda plant_id: {"ok": True, "details": []},
            )

        self.assertEqual(result["state"], "succeeded")
        self.assertAlmostEqual(read_point_internal(client, endpoint_cfg, "p_setpoint"), 12.5, places=6)
        self.assertAlmostEqual(read_point_internal(client, endpoint_cfg, "q_setpoint"), -3.0, places=6)
        self.assertEqual(client.registers[12], 0)
        self.assertEqual(sleep_mock.call_count, 2)

    def test_start_one_plant_enable_failure_rolls_back_state(self):
        shared_data = _shared_data()

        result = _start_one_plant(
            {"STARTUP_INITIAL_SOC_PU": 0.5},
            shared_data,
            "lib",
            tz=timezone.utc,
            set_enable_fn=lambda plant_id, value: False,
            send_setpoints_fn=lambda plant_id, p_kw, q_kvar: True,
            get_latest_schedule_setpoint_fn=lambda plant_id: (1.0, 2.0),
        )

        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["message"], "enable_failed")
        self.assertFalse(shared_data["scheduler_running_by_plant"]["lib"])
        self.assertEqual(shared_data["plant_transition_by_plant"]["lib"], "stopped")

    def test_start_one_plant_command_prepare_failure_fails_fast(self):
        shared_data = _shared_data()
        calls = []

        result = _start_one_plant(
            {"STARTUP_INITIAL_SOC_PU": 0.5},
            shared_data,
            "lib",
            tz=timezone.utc,
            set_enable_fn=lambda plant_id, value: calls.append(("enable", plant_id, value)) or True,
            send_setpoints_fn=lambda plant_id, p_kw, q_kvar: calls.append(("setpoints", plant_id, p_kw, q_kvar)) or True,
            prepare_start_commands_fn=lambda plant_id: {
                "ok": False,
                "details": [{"point": "start_command", "state": "failed", "value": 2, "message": "write_failed"}],
            },
        )

        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["message"], "command_prepare_failed")
        self.assertFalse(result["result"]["command_prepare_ok"])
        self.assertEqual(shared_data["plant_transition_by_plant"]["lib"], "stopped")
        self.assertEqual(calls, [])

    def test_dispatch_enable_disable_commands_mutate_only_scheduler_gate(self):
        shared_data = _shared_data()
        shared_data["plant_transition_by_plant"]["lib"] = "running"

        out_enable = _execute_command(
            {"PLANT_IDS": ("lib", "vrfb")},
            shared_data,
            {"kind": "plant.dispatch_enable", "payload": {"plant_id": "lib"}},
            plant_ids=("lib", "vrfb"),
            tz=timezone.utc,
        )
        self.assertEqual(out_enable["state"], "succeeded")
        self.assertTrue(shared_data["scheduler_running_by_plant"]["lib"])
        self.assertEqual(shared_data["plant_transition_by_plant"]["lib"], "running")

        out_disable = _execute_command(
            {"PLANT_IDS": ("lib", "vrfb")},
            shared_data,
            {"kind": "plant.dispatch_disable", "payload": {"plant_id": "lib"}},
            plant_ids=("lib", "vrfb"),
            tz=timezone.utc,
        )
        self.assertEqual(out_disable["state"], "succeeded")
        self.assertFalse(shared_data["scheduler_running_by_plant"]["lib"])
        self.assertEqual(shared_data["plant_transition_by_plant"]["lib"], "running")

    def test_stop_one_plant_success_uses_safe_stop_result(self):
        shared_data = _shared_data()
        shared_data["plant_transition_by_plant"]["lib"] = "running"
        shared_data["scheduler_running_by_plant"]["lib"] = True

        result = _stop_one_plant(
            {"PLANT_IDS": ("lib", "vrfb")},
            shared_data,
            "lib",
            safe_stop_plant_fn=lambda plant_id: {"threshold_reached": True, "disable_ok": True},
        )

        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(result["result"], {"threshold_reached": True, "disable_ok": True})

    @patch("modbus.setpoint_io._sleep")
    @patch("control.modbus_io.ModbusClient")
    def test_safe_stop_zero_write_uses_trigger_sequence_when_configured(self, client_cls, sleep_mock):
        client = _MemoryModbusClient("127.0.0.1", 502)
        client_cls.return_value = client
        shared_data = _shared_data()
        shared_data["scheduler_running_by_plant"]["lib"] = True
        endpoint_cfg = {
            "host": "127.0.0.1",
            "port": 502,
            "mode": "remote",
            "byte_order": "big",
            "word_order": "msw_first",
            "points": {
                "p_setpoint": {"name": "p_setpoint", "address": 10, "format": "int16", "word_count": 1, "unit": "kW", "eng_per_count": 0.1},
                "q_setpoint": {"name": "q_setpoint", "address": 11, "format": "int16", "word_count": 1, "unit": "kvar", "eng_per_count": 0.1},
                "trigger": {"name": "trigger", "address": 12, "format": "uint16", "word_count": 1, "unit": "raw", "eng_per_count": 1.0},
            },
        }

        with patch("control.engine_agent._get_plant_modbus_config", return_value=endpoint_cfg), patch(
            "control.engine_agent._wait_until_battery_power_below_threshold", return_value=True
        ), patch("control.engine_agent._set_enable", return_value=True), patch(
            "control.engine_agent._run_stop_command_sequence", return_value={"ok": True, "details": []}
        ):
            result = _safe_stop_plant({"PLANT_IDS": ("lib", "vrfb")}, shared_data, "lib")

        self.assertTrue(result["threshold_reached"])
        self.assertTrue(result["disable_ok"])
        self.assertAlmostEqual(read_point_internal(client, endpoint_cfg, "p_setpoint"), 0.0, places=6)
        self.assertAlmostEqual(read_point_internal(client, endpoint_cfg, "q_setpoint"), 0.0, places=6)
        self.assertEqual(client.registers[12], 0)
        self.assertEqual(sleep_mock.call_count, 2)

    def test_stop_one_plant_fails_when_command_stop_failed(self):
        shared_data = _shared_data()
        shared_data["plant_transition_by_plant"]["lib"] = "running"
        shared_data["scheduler_running_by_plant"]["lib"] = True

        result = _stop_one_plant(
            {"PLANT_IDS": ("lib", "vrfb")},
            shared_data,
            "lib",
            safe_stop_plant_fn=lambda plant_id: {
                "threshold_reached": True,
                "disable_ok": True,
                "command_stop_ok": False,
                "command_stop_detail": [{"point": "stop_command", "state": "failed", "value": 1, "message": "write_failed"}],
            },
        )

        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["message"], "command_stop_failed")
        self.assertEqual(shared_data["plant_transition_by_plant"]["lib"], "unknown")

    def test_record_start_and_stop_are_idempotent(self):
        shared_data = _shared_data()
        config = {"PLANT_IDS": ("lib", "vrfb")}
        command_start = {"kind": "plant.record_start", "payload": {"plant_id": "lib"}}

        out1 = _execute_command(
            config,
            shared_data,
            command_start,
            plant_ids=("lib", "vrfb"),
            tz=timezone.utc,
            deps={"get_daily_recording_file_path_fn": lambda plant_id: "data/same.csv"},
        )
        out2 = _execute_command(
            config,
            shared_data,
            command_start,
            plant_ids=("lib", "vrfb"),
            tz=timezone.utc,
            deps={"get_daily_recording_file_path_fn": lambda plant_id: "data/same.csv"},
        )

        self.assertEqual(out1["state"], "succeeded")
        self.assertFalse(out1["result"]["noop"])
        self.assertTrue(out2["result"]["noop"])

        command_stop = {"kind": "plant.record_stop", "payload": {"plant_id": "lib"}}
        out3 = _execute_command(config, shared_data, command_stop, plant_ids=("lib", "vrfb"), tz=timezone.utc)
        out4 = _execute_command(config, shared_data, command_stop, plant_ids=("lib", "vrfb"), tz=timezone.utc)
        self.assertFalse(out3["result"]["noop"])
        self.assertTrue(out4["result"]["noop"])

    def test_fleet_start_all_orders_recording_before_starts_in_remote_mode(self):
        shared_data = _shared_data()
        call_order = []

        def _start_one(plant_id):
            call_order.append(
                (
                    "start",
                    plant_id,
                    dict(shared_data["measurements_filename_by_plant"]),
                    dict(shared_data["scheduler_running_by_plant"]),
                    {
                        "lib": dict(shared_data["dispatch_write_status_by_plant"]["lib"]),
                        "vrfb": dict(shared_data["dispatch_write_status_by_plant"]["vrfb"]),
                    },
                )
            )
            return {"state": "succeeded", "message": None, "result": {"plant_id": plant_id}}

        result = _execute_command(
            {"PLANT_IDS": ("lib", "vrfb")},
            shared_data,
            {"kind": "fleet.start_all", "payload": {}},
            plant_ids=("lib", "vrfb"),
            tz=timezone.utc,
            deps={
                "start_one_plant_fn": _start_one,
                "get_daily_recording_file_path_fn": lambda plant_id: f"data/{plant_id}.csv",
            },
        )

        self.assertEqual(result["state"], "succeeded")
        self.assertEqual([item[0:2] for item in call_order], [("start", "lib"), ("start", "vrfb")])
        for _, plant_id, recording_map, dispatch_map, dispatch_status_map in call_order:
            self.assertEqual(recording_map["lib"], "data/lib.csv")
            self.assertEqual(recording_map["vrfb"], "data/vrfb.csv")
            self.assertTrue(dispatch_map[plant_id])
            self.assertTrue(dispatch_status_map[plant_id]["sending_enabled"])
        self.assertTrue(call_order[-1][3]["lib"])
        self.assertTrue(call_order[-1][3]["vrfb"])
        self.assertTrue(call_order[-1][4]["lib"]["sending_enabled"])
        self.assertTrue(call_order[-1][4]["vrfb"]["sending_enabled"])

    def test_fleet_start_all_orders_starts_before_recording_in_local_mode(self):
        shared_data = _shared_data()
        shared_data["transport_mode"] = "local"
        shared_data["measurements_filename_by_plant"]["lib"] = None
        shared_data["measurements_filename_by_plant"]["vrfb"] = None
        call_order = []

        def _start_one(plant_id):
            call_order.append(
                (
                    "start",
                    plant_id,
                    dict(shared_data["measurements_filename_by_plant"]),
                    dict(shared_data["scheduler_running_by_plant"]),
                    {
                        "lib": dict(shared_data["dispatch_write_status_by_plant"]["lib"]),
                        "vrfb": dict(shared_data["dispatch_write_status_by_plant"]["vrfb"]),
                    },
                )
            )
            return {"state": "succeeded", "message": None, "result": {"plant_id": plant_id}}

        result = _execute_command(
            {"PLANT_IDS": ("lib", "vrfb")},
            shared_data,
            {"kind": "fleet.start_all", "payload": {}},
            plant_ids=("lib", "vrfb"),
            tz=timezone.utc,
            deps={
                "start_one_plant_fn": _start_one,
                "get_daily_recording_file_path_fn": lambda plant_id: f"data/{plant_id}.csv",
            },
        )

        self.assertEqual(result["state"], "succeeded")
        self.assertEqual([item[0:2] for item in call_order], [("start", "lib"), ("start", "vrfb")])
        for _, plant_id, recording_map, dispatch_map, dispatch_status_map in call_order:
            self.assertIsNone(recording_map["lib"])
            self.assertIsNone(recording_map["vrfb"])
            self.assertTrue(dispatch_map[plant_id])
            self.assertTrue(dispatch_status_map[plant_id]["sending_enabled"])
        self.assertEqual(shared_data["measurements_filename_by_plant"]["lib"], "data/lib.csv")
        self.assertEqual(shared_data["measurements_filename_by_plant"]["vrfb"], "data/vrfb.csv")
        self.assertTrue(call_order[-1][3]["lib"])
        self.assertTrue(call_order[-1][3]["vrfb"])
        self.assertTrue(call_order[-1][4]["lib"]["sending_enabled"])
        self.assertTrue(call_order[-1][4]["vrfb"]["sending_enabled"])

    def test_fleet_start_all_local_partial_failure_still_enables_recording(self):
        shared_data = _shared_data()
        shared_data["transport_mode"] = "local"
        shared_data["measurements_filename_by_plant"]["lib"] = None
        shared_data["measurements_filename_by_plant"]["vrfb"] = None

        def _start_one(plant_id):
            if plant_id == "vrfb":
                return {"state": "failed", "message": "enable_failed", "result": {"plant_id": plant_id}}
            return {"state": "succeeded", "message": None, "result": {"plant_id": plant_id}}

        result = _execute_command(
            {"PLANT_IDS": ("lib", "vrfb")},
            shared_data,
            {"kind": "fleet.start_all", "payload": {}},
            plant_ids=("lib", "vrfb"),
            tz=timezone.utc,
            deps={
                "start_one_plant_fn": _start_one,
                "get_daily_recording_file_path_fn": lambda plant_id: f"data/{plant_id}.csv",
            },
        )

        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["message"], "fleet_start_partial_failure")
        self.assertEqual(result["result"]["per_plant"]["lib"]["state"], "succeeded")
        self.assertEqual(result["result"]["per_plant"]["vrfb"]["state"], "failed")
        self.assertEqual(shared_data["measurements_filename_by_plant"]["lib"], "data/lib.csv")
        self.assertEqual(shared_data["measurements_filename_by_plant"]["vrfb"], "data/vrfb.csv")
        self.assertTrue(shared_data["scheduler_running_by_plant"]["lib"])
        self.assertTrue(shared_data["scheduler_running_by_plant"]["vrfb"])

    def test_fleet_stop_all_orders_safe_stop_before_recording_clear(self):
        shared_data = _shared_data()
        observed = {}
        shared_data["scheduler_running_by_plant"]["lib"] = True
        shared_data["scheduler_running_by_plant"]["vrfb"] = True
        shared_data["dispatch_write_status_by_plant"]["lib"]["sending_enabled"] = True
        shared_data["dispatch_write_status_by_plant"]["vrfb"]["sending_enabled"] = True

        def _safe_stop_all():
            observed["recording_before"] = dict(shared_data["measurements_filename_by_plant"])
            shared_data["scheduler_running_by_plant"]["lib"] = False
            shared_data["scheduler_running_by_plant"]["vrfb"] = False
            return {
                "lib": {"threshold_reached": True, "disable_ok": True},
                "vrfb": {"threshold_reached": True, "disable_ok": True},
            }

        result = _execute_command(
            {"PLANT_IDS": ("lib", "vrfb")},
            shared_data,
            {"kind": "fleet.stop_all", "payload": {}},
            plant_ids=("lib", "vrfb"),
            tz=timezone.utc,
            deps={"safe_stop_all_plants_fn": _safe_stop_all},
        )

        self.assertEqual(result["state"], "succeeded")
        self.assertIsNotNone(observed["recording_before"]["lib"])
        self.assertIsNone(shared_data["measurements_filename_by_plant"]["lib"])
        self.assertIsNone(shared_data["measurements_filename_by_plant"]["vrfb"])
        self.assertFalse(shared_data["scheduler_running_by_plant"]["lib"])
        self.assertFalse(shared_data["scheduler_running_by_plant"]["vrfb"])
        self.assertFalse(shared_data["dispatch_write_status_by_plant"]["lib"]["sending_enabled"])
        self.assertFalse(shared_data["dispatch_write_status_by_plant"]["vrfb"]["sending_enabled"])

    def test_transport_switch_noop_when_mode_matches(self):
        shared_data = _shared_data()
        shared_data["transport_mode"] = "remote"

        result = _execute_command(
            {"PLANT_IDS": ("lib", "vrfb")},
            shared_data,
            {"kind": "transport.switch", "payload": {"mode": "remote"}},
            plant_ids=("lib", "vrfb"),
            tz=timezone.utc,
        )

        self.assertEqual(result["state"], "succeeded")
        self.assertTrue(result["result"]["noop"])

    def test_command_status_progresses_through_engine_cycle(self):
        shared_data = _shared_data()
        now_value = datetime(2026, 2, 25, 12, 0, tzinfo=timezone.utc)
        status = enqueue_control_command(
            shared_data,
            kind="plant.start",
            payload={"plant_id": "lib"},
            source="dashboard",
            now_fn=lambda: now_value,
        )
        self.assertEqual(shared_data["control_command_status_by_id"][status["id"]]["state"], "queued")

        _run_single_engine_cycle(
            {"PLANT_IDS": ("lib", "vrfb")},
            shared_data,
            plant_ids=("lib", "vrfb"),
            tz=timezone.utc,
            now_fn=lambda _config: now_value,
            deps={
                "refresh_all_observed_state_fn": lambda: None,
                "start_one_plant_fn": lambda plant_id: {"state": "succeeded", "message": None, "result": {"plant_id": plant_id}},
            },
        )

        final_status = shared_data["control_command_status_by_id"][status["id"]]
        self.assertEqual(final_status["state"], "succeeded")
        self.assertIsNotNone(final_status["started_at"])
        self.assertIsNotNone(final_status["finished_at"])
        self.assertIsNone(shared_data["control_command_active_id"])
        engine_status = shared_data["control_engine_status"]
        self.assertTrue(engine_status["alive"])
        self.assertEqual(engine_status["queue_depth"], 0)
        self.assertEqual(engine_status["queued_count"], 0)
        self.assertEqual(engine_status["running_count"], 0)
        self.assertIsNotNone(engine_status["last_loop_start"])
        self.assertIsNotNone(engine_status["last_loop_end"])
        self.assertIsNotNone(engine_status["last_observed_refresh"])
        self.assertEqual(engine_status["last_finished_command"]["id"], status["id"])
        self.assertEqual(engine_status["last_finished_command"]["state"], "succeeded")

    def test_publish_observed_state_preserves_values_on_failure_and_marks_stale(self):
        shared_data = _shared_data()
        t0 = datetime(2026, 2, 25, 12, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(seconds=1)
        t2 = t0 + timedelta(seconds=5)

        _publish_observed_state(
            shared_data,
            "lib",
            {"enable_state": 1, "start_command_state": 2, "stop_command_state": 0, "p_battery_kw": 4.0, "q_battery_kvar": -1.0},
            now_value=t0,
        )
        failed = _publish_observed_state(
            shared_data,
            "lib",
            {"enable_state": None, "p_battery_kw": None, "q_battery_kvar": None},
            error="connect_failed",
            now_value=t1,
        )
        stale = _publish_observed_state(
            shared_data,
            "lib",
            {"enable_state": None, "p_battery_kw": None, "q_battery_kvar": None},
            error="still_failed",
            now_value=t2,
        )

        self.assertEqual(failed["enable_state"], 1)
        self.assertEqual(failed["start_command_state"], 2)
        self.assertEqual(failed["stop_command_state"], 0)
        self.assertEqual(failed["p_battery_kw"], 4.0)
        self.assertEqual(failed["error"], "connect_failed")
        self.assertEqual(failed["read_status"], "connect_failed")
        self.assertEqual(failed["consecutive_failures"], 1)
        self.assertIsNotNone(failed["last_error"])
        self.assertFalse(failed["stale"])
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["consecutive_failures"], 2)

    def test_publish_observed_state_classifies_dict_error_and_resets_on_success(self):
        shared_data = _shared_data()
        t0 = datetime(2026, 2, 25, 12, 0, tzinfo=timezone.utc)
        t1 = t0 + timedelta(seconds=1)

        failed = _publish_observed_state(
            shared_data,
            "lib",
            {"enable_state": None, "p_battery_kw": None, "q_battery_kvar": None},
            error={"code": "read_error", "message": "boom"},
            now_value=t0,
        )
        recovered = _publish_observed_state(
            shared_data,
            "lib",
            {"enable_state": 0, "start_command_state": 1, "stop_command_state": 0, "p_battery_kw": 0.0, "q_battery_kvar": 0.0},
            now_value=t1,
        )

        self.assertEqual(failed["read_status"], "read_error")
        self.assertEqual(failed["error"], "boom")
        self.assertEqual(failed["consecutive_failures"], 1)
        self.assertEqual(recovered["read_status"], "ok")
        self.assertIsNone(recovered["error"])
        self.assertEqual(recovered["consecutive_failures"], 0)
        self.assertEqual(recovered["enable_state"], 0)
        self.assertEqual(recovered["start_command_state"], 1)
        self.assertEqual(recovered["stop_command_state"], 0)

    def test_engine_cycle_publishes_last_exception_on_command_crash(self):
        shared_data = _shared_data()
        now_value = datetime(2026, 2, 25, 12, 0, tzinfo=timezone.utc)
        enqueue_control_command(
            shared_data,
            kind="plant.start",
            payload={"plant_id": "lib"},
            source="dashboard",
            now_fn=lambda: now_value,
        )

        _run_single_engine_cycle(
            {"PLANT_IDS": ("lib", "vrfb")},
            shared_data,
            plant_ids=("lib", "vrfb"),
            tz=timezone.utc,
            now_fn=lambda _config: now_value,
            deps={
                "refresh_all_observed_state_fn": lambda: None,
                "start_one_plant_fn": lambda plant_id: (_ for _ in ()).throw(RuntimeError("forced command crash")),
            },
        )

        engine_status = shared_data["control_engine_status"]
        self.assertIsNotNone(engine_status["last_exception"])
        self.assertIn("forced command crash", engine_status["last_exception"]["message"])
        self.assertEqual(engine_status["failed_recent_count"], 1)


if __name__ == "__main__":
    unittest.main()
