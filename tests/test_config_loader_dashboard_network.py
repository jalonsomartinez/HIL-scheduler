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


class ConfigLoaderDashboardNetworkTests(unittest.TestCase):
    def test_defaults_expose_private_and_public_dashboard_settings(self):
        config = load_config("config.yaml")
        self.assertEqual(config["DASHBOARD_PRIVATE_HOST"], "127.0.0.1")
        self.assertEqual(config["DASHBOARD_PRIVATE_PORT"], 8050)
        self.assertEqual(config["DASHBOARD_PUBLIC_READONLY_ENABLED"], False)
        self.assertEqual(config["DASHBOARD_PUBLIC_READONLY_HOST"], "127.0.0.1")
        self.assertEqual(config["DASHBOARD_PUBLIC_READONLY_PORT"], 8060)
        self.assertEqual(config["DASHBOARD_PUBLIC_READONLY_AUTH_MODE"], "basic")

    def test_custom_values_override_dashboard_settings(self):
        payload = _load_yaml("config.yaml")
        payload.setdefault("dashboard", {})["private"] = {"host": "0.0.0.0", "port": 9000}
        payload["dashboard"]["public_readonly"] = {
            "enabled": True,
            "host": "127.0.0.2",
            "port": 9001,
            "auth": {"mode": "none"},
        }
        path = _write_temp_yaml(payload)
        try:
            config = load_config(path)
        finally:
            os.unlink(path)

        self.assertEqual(config["DASHBOARD_PRIVATE_HOST"], "0.0.0.0")
        self.assertEqual(config["DASHBOARD_PRIVATE_PORT"], 9000)
        self.assertTrue(config["DASHBOARD_PUBLIC_READONLY_ENABLED"])
        self.assertEqual(config["DASHBOARD_PUBLIC_READONLY_HOST"], "127.0.0.2")
        self.assertEqual(config["DASHBOARD_PUBLIC_READONLY_PORT"], 9001)
        self.assertEqual(config["DASHBOARD_PUBLIC_READONLY_AUTH_MODE"], "none")

    def test_invalid_auth_mode_falls_back_to_basic(self):
        payload = _load_yaml("config.yaml")
        payload.setdefault("dashboard", {}).setdefault("public_readonly", {}).setdefault("auth", {})["mode"] = "invalid"
        path = _write_temp_yaml(payload)
        try:
            config = load_config(path)
        finally:
            os.unlink(path)

        self.assertEqual(config["DASHBOARD_PUBLIC_READONLY_AUTH_MODE"], "basic")


if __name__ == "__main__":
    unittest.main()
