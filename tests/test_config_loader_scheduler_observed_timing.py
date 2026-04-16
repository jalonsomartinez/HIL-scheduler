import os
import tempfile
import unittest

import yaml

from config_loader import load_config


def _load_yaml(path):
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _write_temp_yaml(data):
    handle = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8")
    try:
        yaml.safe_dump(data, handle, sort_keys=False)
        return handle.name
    finally:
        handle.close()


class ConfigLoaderSchedulerObservedTimingTests(unittest.TestCase):
    def test_load_config_exposes_observed_state_and_scheduler_retry_defaults(self):
        payload = _load_yaml("config_test.yaml")
        timing_cfg = payload.setdefault("timing", {})
        timing_cfg.pop("observed_state_poll_period_s", None)
        timing_cfg.pop("observed_state_phase_offset_s", None)
        timing_cfg.pop("observed_state_stale_after_s", None)
        timing_cfg.pop("scheduler_phase_offset_s", None)
        timing_cfg.pop("measurement_phase_offset_s", None)
        timing_cfg.pop("grid_map_phase_offset_s", None)
        timing_cfg.pop("scheduler_failed_write_retry_initial_s", None)
        timing_cfg.pop("scheduler_failed_write_retry_max_s", None)
        timing_cfg.pop("scheduler_failed_write_retry_multiplier", None)
        path = _write_temp_yaml(payload)
        try:
            config = load_config(path)
        finally:
            os.unlink(path)
        self.assertEqual(float(config["OBSERVED_STATE_POLL_PERIOD_S"]), 1.0)
        self.assertEqual(float(config["OBSERVED_STATE_PHASE_OFFSET_S"]), 0.0)
        self.assertEqual(float(config["OBSERVED_STATE_STALE_AFTER_S"]), 3.0)
        self.assertEqual(float(config["SCHEDULER_PHASE_OFFSET_S"]), 0.0)
        self.assertEqual(float(config["MEASUREMENT_PHASE_OFFSET_S"]), 0.0)
        self.assertEqual(float(config["GRID_MAP_PHASE_OFFSET_S"]), 0.0)
        self.assertEqual(float(config["SCHEDULER_FAILED_WRITE_RETRY_INITIAL_S"]), 5.0)
        self.assertEqual(float(config["SCHEDULER_FAILED_WRITE_RETRY_MAX_S"]), 20.0)
        self.assertEqual(float(config["SCHEDULER_FAILED_WRITE_RETRY_MULTIPLIER"]), 2.0)

    def test_accepts_custom_observed_state_and_scheduler_retry_timing(self):
        payload = _load_yaml("config_test.yaml")
        timing_cfg = payload.setdefault("timing", {})
        timing_cfg["observed_state_poll_period_s"] = 7.5
        timing_cfg["observed_state_phase_offset_s"] = 0.5
        timing_cfg["observed_state_stale_after_s"] = 18.0
        timing_cfg["scheduler_phase_offset_s"] = 1.0
        timing_cfg["measurement_phase_offset_s"] = 2.5
        timing_cfg["grid_map_phase_offset_s"] = 4.0
        timing_cfg["scheduler_failed_write_retry_initial_s"] = 3.0
        timing_cfg["scheduler_failed_write_retry_max_s"] = 9.0
        timing_cfg["scheduler_failed_write_retry_multiplier"] = 1.5
        path = _write_temp_yaml(payload)
        try:
            config = load_config(path)
        finally:
            os.unlink(path)
        self.assertEqual(float(config["OBSERVED_STATE_POLL_PERIOD_S"]), 7.5)
        self.assertEqual(float(config["OBSERVED_STATE_PHASE_OFFSET_S"]), 0.5)
        self.assertEqual(float(config["OBSERVED_STATE_STALE_AFTER_S"]), 18.0)
        self.assertEqual(float(config["SCHEDULER_PHASE_OFFSET_S"]), 1.0)
        self.assertEqual(float(config["MEASUREMENT_PHASE_OFFSET_S"]), 2.5)
        self.assertEqual(float(config["GRID_MAP_PHASE_OFFSET_S"]), 4.0)
        self.assertEqual(float(config["SCHEDULER_FAILED_WRITE_RETRY_INITIAL_S"]), 3.0)
        self.assertEqual(float(config["SCHEDULER_FAILED_WRITE_RETRY_MAX_S"]), 9.0)
        self.assertEqual(float(config["SCHEDULER_FAILED_WRITE_RETRY_MULTIPLIER"]), 1.5)

    def test_invalid_values_fall_back_to_defaults(self):
        payload = _load_yaml("config_test.yaml")
        timing_cfg = payload.setdefault("timing", {})
        timing_cfg["observed_state_poll_period_s"] = 0
        timing_cfg["observed_state_phase_offset_s"] = -1
        timing_cfg["observed_state_stale_after_s"] = "bad"
        timing_cfg["scheduler_phase_offset_s"] = -1
        timing_cfg["measurement_phase_offset_s"] = -2
        timing_cfg["grid_map_phase_offset_s"] = -3
        timing_cfg["scheduler_failed_write_retry_initial_s"] = 0
        timing_cfg["scheduler_failed_write_retry_max_s"] = -1
        timing_cfg["scheduler_failed_write_retry_multiplier"] = 0
        path = _write_temp_yaml(payload)
        try:
            config = load_config(path)
        finally:
            os.unlink(path)
        self.assertEqual(float(config["OBSERVED_STATE_POLL_PERIOD_S"]), 1.0)
        self.assertEqual(float(config["OBSERVED_STATE_PHASE_OFFSET_S"]), 0.0)
        self.assertEqual(float(config["OBSERVED_STATE_STALE_AFTER_S"]), 3.0)
        self.assertEqual(float(config["SCHEDULER_PHASE_OFFSET_S"]), 0.0)
        self.assertEqual(float(config["MEASUREMENT_PHASE_OFFSET_S"]), 0.0)
        self.assertEqual(float(config["GRID_MAP_PHASE_OFFSET_S"]), 0.0)
        self.assertEqual(float(config["SCHEDULER_FAILED_WRITE_RETRY_INITIAL_S"]), 5.0)
        self.assertEqual(float(config["SCHEDULER_FAILED_WRITE_RETRY_MAX_S"]), 20.0)
        self.assertEqual(float(config["SCHEDULER_FAILED_WRITE_RETRY_MULTIPLIER"]), 2.0)

    def test_retry_max_is_never_below_retry_initial(self):
        payload = _load_yaml("config_test.yaml")
        timing_cfg = payload.setdefault("timing", {})
        timing_cfg["scheduler_failed_write_retry_initial_s"] = 8.0
        timing_cfg["scheduler_failed_write_retry_max_s"] = 4.0
        path = _write_temp_yaml(payload)
        try:
            config = load_config(path)
        finally:
            os.unlink(path)
        self.assertEqual(float(config["SCHEDULER_FAILED_WRITE_RETRY_INITIAL_S"]), 8.0)
        self.assertEqual(float(config["SCHEDULER_FAILED_WRITE_RETRY_MAX_S"]), 8.0)


if __name__ == "__main__":
    unittest.main()
