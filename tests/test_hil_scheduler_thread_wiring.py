import threading
import unittest

from hil_scheduler import build_agent_threads


class HilSchedulerThreadWiringTests(unittest.TestCase):
    def test_build_agent_threads_excludes_public_dashboard_when_disabled(self):
        config = {"DASHBOARD_PUBLIC_READONLY_ENABLED": False}
        shared_data = {"lock": threading.Lock()}

        threads = build_agent_threads(config, shared_data)
        targets = [getattr(thread, "_target", None) for thread in threads]
        target_names = {getattr(target, "__name__", "") for target in targets}

        self.assertIn("dashboard_agent", target_names)
        self.assertNotIn("public_dashboard_agent", target_names)

    def test_build_agent_threads_includes_public_dashboard_when_enabled(self):
        config = {"DASHBOARD_PUBLIC_READONLY_ENABLED": True}
        shared_data = {"lock": threading.Lock()}

        threads = build_agent_threads(config, shared_data)
        targets = [getattr(thread, "_target", None) for thread in threads]
        target_names = {getattr(target, "__name__", "") for target in targets}

        self.assertIn("dashboard_agent", target_names)
        self.assertIn("public_dashboard_agent", target_names)


if __name__ == "__main__":
    unittest.main()
