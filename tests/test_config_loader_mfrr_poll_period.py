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


class ConfigLoaderMfrrPollPeriodTests(unittest.TestCase):
    def test_load_config_exposes_mfrr_poll_period(self):
        config = load_config("config.yaml")
        self.assertEqual(float(config["ISTENTORE_MFRR_POLL_PERIOD_S"]), 60.0)

    def test_accepts_custom_mfrr_poll_period(self):
        payload = _load_yaml("config.yaml")
        payload.setdefault("istentore_api", {})["mfrr_poll_period_s"] = 45
        path = _write_temp_yaml(payload)
        try:
            config = load_config(path)
        finally:
            os.unlink(path)
        self.assertEqual(float(config["ISTENTORE_MFRR_POLL_PERIOD_S"]), 45.0)

    def test_non_positive_mfrr_poll_period_falls_back_to_default(self):
        for invalid_value in (0, -1, "0"):
            with self.subTest(value=invalid_value):
                payload = _load_yaml("config.yaml")
                payload.setdefault("istentore_api", {})["mfrr_poll_period_s"] = invalid_value
                path = _write_temp_yaml(payload)
                try:
                    config = load_config(path)
                finally:
                    os.unlink(path)
                self.assertEqual(float(config["ISTENTORE_MFRR_POLL_PERIOD_S"]), 60.0)


if __name__ == "__main__":
    unittest.main()
