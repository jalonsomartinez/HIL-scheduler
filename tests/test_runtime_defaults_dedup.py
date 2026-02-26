import unittest

import config_loader
import measurement.agent as measurement_agent
import time_utils
from runtime.defaults import (
    DEFAULT_MEASUREMENT_COMPRESSION_MAX_KEPT_GAP_S,
    DEFAULT_MEASUREMENT_COMPRESSION_TOLERANCES,
    DEFAULT_TIMEZONE_NAME,
)


class RuntimeDefaultsDedupTests(unittest.TestCase):
    def test_timezone_default_is_shared(self):
        self.assertEqual(time_utils.DEFAULT_TIMEZONE_NAME, DEFAULT_TIMEZONE_NAME)
        self.assertEqual(config_loader.DEFAULT_TIMEZONE_NAME, DEFAULT_TIMEZONE_NAME)

    def test_measurement_compression_defaults_are_shared(self):
        self.assertIs(
            config_loader.DEFAULT_MEASUREMENT_COMPRESSION_TOLERANCES,
            DEFAULT_MEASUREMENT_COMPRESSION_TOLERANCES,
        )
        self.assertIs(
            measurement_agent.DEFAULT_COMPRESSION_TOLERANCES,
            DEFAULT_MEASUREMENT_COMPRESSION_TOLERANCES,
        )
        self.assertEqual(
            config_loader.DEFAULT_MEASUREMENT_COMPRESSION_MAX_KEPT_GAP_S,
            DEFAULT_MEASUREMENT_COMPRESSION_MAX_KEPT_GAP_S,
        )


if __name__ == "__main__":
    unittest.main()
