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


class ConfigLoaderGridMapPeriodTests(unittest.TestCase):
    def test_load_config_exposes_grid_map_period(self):
        config = load_config("config.yaml")
        self.assertEqual(float(config["GRID_MAP_PERIOD_S"]), 5.0)

    def test_accepts_custom_grid_map_period(self):
        payload = _load_yaml("config.yaml")
        payload.setdefault("timing", {})["grid_map_period_s"] = 7.5
        path = _write_temp_yaml(payload)
        try:
            config = load_config(path)
        finally:
            os.unlink(path)
        self.assertEqual(float(config["GRID_MAP_PERIOD_S"]), 7.5)

    def test_non_positive_grid_map_period_falls_back_to_default(self):
        for invalid_value in (0, -1, "0"):
            with self.subTest(value=invalid_value):
                payload = _load_yaml("config.yaml")
                payload.setdefault("timing", {})["grid_map_period_s"] = invalid_value
                path = _write_temp_yaml(payload)
                try:
                    config = load_config(path)
                finally:
                    os.unlink(path)
                self.assertEqual(float(config["GRID_MAP_PERIOD_S"]), 5.0)


if __name__ == "__main__":
    unittest.main()
