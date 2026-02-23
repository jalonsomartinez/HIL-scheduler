import unittest
from unittest.mock import patch

from config_loader import load_config


class ConfigLoaderStartupInitialSocTests(unittest.TestCase):
    def test_load_config_exposes_shared_startup_initial_soc(self):
        config = load_config("config.yaml")

        self.assertIn("STARTUP_INITIAL_SOC_PU", config)
        self.assertEqual(config["STARTUP_INITIAL_SOC_PU"], 0.5)

    def test_plant_models_do_not_include_initial_soc(self):
        config = load_config("config.yaml")

        self.assertNotIn("initial_soc_pu", config["PLANTS"]["lib"]["model"])
        self.assertNotIn("initial_soc_pu", config["PLANTS"]["vrfb"]["model"])

    def test_legacy_alias_initial_soc_maps_to_startup_initial_soc(self):
        with patch.dict("os.environ", {"HIL_ENABLE_LEGACY_CONFIG_ALIASES": "1"}, clear=False):
            config = load_config("config.yaml")

        self.assertIn("PLANT_INITIAL_SOC_PU", config)
        self.assertEqual(config["PLANT_INITIAL_SOC_PU"], config["STARTUP_INITIAL_SOC_PU"])


if __name__ == "__main__":
    unittest.main()
