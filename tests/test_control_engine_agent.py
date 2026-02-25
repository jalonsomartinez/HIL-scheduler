import queue
import threading
import unittest
from datetime import datetime, timedelta, timezone

from control_command_runtime import enqueue_control_command
from control_engine_agent import (
    _execute_command,
    _publish_observed_state,
    _run_single_engine_cycle,
    _start_one_plant,
    _stop_one_plant,
)


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
    def test_start_one_plant_success_updates_gate_transition_and_result(self):
        shared_data = _shared_data()
        calls = []

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
        self.assertTrue(result["result"]["initial_setpoint_write_ok"])
        self.assertEqual(shared_data["scheduler_running_by_plant"]["lib"], True)
        self.assertEqual(shared_data["plant_transition_by_plant"]["lib"], "running")
        self.assertEqual(calls[0], ("enable", "lib", 1))
        self.assertEqual(calls[1], ("setpoints", "lib", 12.5, -3.0))

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

    def test_fleet_start_all_orders_recording_before_starts(self):
        shared_data = _shared_data()
        call_order = []

        def _start_one(plant_id):
            call_order.append(("start", plant_id, dict(shared_data["measurements_filename_by_plant"])))
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
        for _, _, recording_map in call_order:
            self.assertEqual(recording_map["lib"], "data/lib.csv")
            self.assertEqual(recording_map["vrfb"], "data/vrfb.csv")

    def test_fleet_stop_all_orders_safe_stop_before_recording_clear(self):
        shared_data = _shared_data()
        observed = {}

        def _safe_stop_all():
            observed["recording_before"] = dict(shared_data["measurements_filename_by_plant"])
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
            {"enable_state": 1, "p_battery_kw": 4.0, "q_battery_kvar": -1.0},
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
            {"enable_state": 0, "p_battery_kw": 0.0, "q_battery_kvar": 0.0},
            now_value=t1,
        )

        self.assertEqual(failed["read_status"], "read_error")
        self.assertEqual(failed["error"], "boom")
        self.assertEqual(failed["consecutive_failures"], 1)
        self.assertEqual(recovered["read_status"], "ok")
        self.assertIsNone(recovered["error"])
        self.assertEqual(recovered["consecutive_failures"], 0)
        self.assertEqual(recovered["enable_state"], 0)

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
