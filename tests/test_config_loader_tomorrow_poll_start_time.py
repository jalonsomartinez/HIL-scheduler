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


class ConfigLoaderTomorrowPollStartTimeTests(unittest.TestCase):
    def test_load_config_exposes_tomorrow_poll_start_time(self):
        config = load_config("config.yaml")
        self.assertEqual(config["ISTENTORE_TOMORROW_POLL_START_TIME"], "15:00")
        self.assertNotIn("ISTENTORE_POLL_START_TIME", config)

    def test_normalizes_non_padded_tomorrow_poll_start_time(self):
        payload = _load_yaml("config.yaml")
        payload.setdefault("istentore_api", {})["tomorrow_poll_start_time"] = "9:00"
        path = _write_temp_yaml(payload)
        try:
            config = load_config(path)
        finally:
            os.unlink(path)

        self.assertEqual(config["ISTENTORE_TOMORROW_POLL_START_TIME"], "09:00")

    def test_rejects_invalid_tomorrow_poll_start_time(self):
        for invalid_value in ("24:00", "9", "09:60"):
            with self.subTest(value=invalid_value):
                payload = _load_yaml("config.yaml")
                payload.setdefault("istentore_api", {})["tomorrow_poll_start_time"] = invalid_value
                path = _write_temp_yaml(payload)
                try:
                    with self.assertRaisesRegex(ValueError, "tomorrow_poll_start_time"):
                        load_config(path)
                finally:
                    os.unlink(path)

    def test_rejects_legacy_poll_start_time_key(self):
        payload = _load_yaml("config.yaml")
        api_cfg = payload.setdefault("istentore_api", {})
        api_cfg.pop("tomorrow_poll_start_time", None)
        api_cfg["poll_start_time"] = "15:00"
        path = _write_temp_yaml(payload)
        try:
            with self.assertRaisesRegex(ValueError, "poll_start_time'.*tomorrow_poll_start_time"):
                load_config(path)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
