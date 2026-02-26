import os
import tempfile
import unittest

from runtime.paths import get_assets_dir, get_data_dir, get_logs_dir, get_project_root


class RuntimePathsTests(unittest.TestCase):
    def test_resolves_actual_repo_root_from_test_file(self):
        root = get_project_root(__file__)
        self.assertTrue(os.path.isfile(os.path.join(root, "hil_scheduler.py")))
        self.assertTrue(os.path.isdir(os.path.join(root, "assets")))

    def test_directory_helpers_point_to_repo_root_subdirs(self):
        root = get_project_root(__file__)
        self.assertEqual(get_assets_dir(__file__), os.path.join(root, "assets"))
        self.assertEqual(get_logs_dir(__file__), os.path.join(root, "logs"))
        self.assertEqual(get_data_dir(__file__), os.path.join(root, "data"))

    def test_resolves_parent_when_anchor_is_dashboard_package_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, "assets"), exist_ok=True)
            os.makedirs(os.path.join(tmpdir, "dashboard"), exist_ok=True)
            os.makedirs(os.path.join(tmpdir, "memory-bank"), exist_ok=True)
            with open(os.path.join(tmpdir, "config.yaml"), "w", encoding="utf-8") as handle:
                handle.write("general: {}\\n")

            dashboard_dir = os.path.join(tmpdir, "dashboard")
            self.assertEqual(get_project_root(dashboard_dir), tmpdir)
            self.assertEqual(get_assets_dir(dashboard_dir), os.path.join(tmpdir, "assets"))
            self.assertEqual(get_logs_dir(dashboard_dir), os.path.join(tmpdir, "logs"))
            self.assertEqual(get_data_dir(dashboard_dir), os.path.join(tmpdir, "data"))


if __name__ == "__main__":
    unittest.main()
