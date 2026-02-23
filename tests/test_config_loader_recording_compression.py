import unittest

from config_loader import load_config


class ConfigLoaderRecordingCompressionTests(unittest.TestCase):
    def test_load_config_exposes_compression_max_kept_gap(self):
        config = load_config("config.yaml")

        self.assertIn("MEASUREMENT_COMPRESSION_MAX_KEPT_GAP_S", config)
        self.assertGreaterEqual(config["MEASUREMENT_COMPRESSION_MAX_KEPT_GAP_S"], 0.0)
        self.assertEqual(config["MEASUREMENT_COMPRESSION_MAX_KEPT_GAP_S"], 3600.0)


if __name__ == "__main__":
    unittest.main()
