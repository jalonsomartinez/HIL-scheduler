import unittest

import config_loader
import hil_scheduler
import measurement.agent as measurement_agent
import time_utils
from runtime.defaults import (
    DEFAULT_MEASUREMENT_COMPRESSION_MAX_KEPT_GAP_S,
    DEFAULT_MEASUREMENT_COMPRESSION_TOLERANCES,
    DEFAULT_TIMEZONE_NAME,
    default_measurement_post_status,
    default_measurement_post_status_by_plant,
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

    def test_measurement_post_status_defaults_are_shared(self):
        self.assertIs(measurement_agent.default_measurement_post_status, default_measurement_post_status)
        self.assertIs(hil_scheduler.default_measurement_post_status_by_plant, default_measurement_post_status_by_plant)

        first = default_measurement_post_status()
        second = default_measurement_post_status()
        self.assertIsNot(first, second)
        self.assertEqual(first, second)
        self.assertEqual(
            set(first.keys()),
            {
                "posting_enabled",
                "last_success",
                "last_attempt",
                "last_error",
                "pending_queue_count",
                "oldest_pending_age_s",
                "last_enqueue",
            },
        )


if __name__ == "__main__":
    unittest.main()
