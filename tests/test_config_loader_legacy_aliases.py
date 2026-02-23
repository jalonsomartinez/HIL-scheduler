import unittest
from unittest.mock import patch

from config_loader import load_config


class ConfigLoaderLegacyAliasTests(unittest.TestCase):
    def test_legacy_aliases_disabled_by_default(self):
        with patch.dict("os.environ", {}, clear=False):
            config = load_config("config.yaml")

        self.assertNotIn("TRANSPORT_MODE", config)
        self.assertNotIn("STARTUP_PLANT", config)
        self.assertNotIn("PLANT_P_MAX_KW", config)
        self.assertNotIn("ISTENTORE_MEASUREMENT_SERIES_LOCAL_SOC_ID", config)

    def test_legacy_aliases_enabled_with_env_var(self):
        with patch.dict("os.environ", {"HIL_ENABLE_LEGACY_CONFIG_ALIASES": "1"}, clear=False):
            config = load_config("config.yaml")

        self.assertEqual(config.get("TRANSPORT_MODE"), config.get("STARTUP_TRANSPORT_MODE"))
        self.assertEqual(config.get("STARTUP_PLANT"), config.get("STARTUP_TRANSPORT_MODE"))
        self.assertIn("PLANT_P_MAX_KW", config)
        self.assertIn("PLANT_INITIAL_SOC_PU", config)
        self.assertEqual(config["PLANT_INITIAL_SOC_PU"], config["STARTUP_INITIAL_SOC_PU"])
        self.assertIn("ISTENTORE_MEASUREMENT_SERIES_LOCAL_SOC_ID", config)


if __name__ == "__main__":
    unittest.main()
