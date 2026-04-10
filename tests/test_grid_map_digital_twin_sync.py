import unittest
from pathlib import Path


class GridMapDigitalTwinSyncTests(unittest.TestCase):
    def test_grid_map_digital_twin_mirrors_source_package_files(self):
        repo_root = Path(__file__).resolve().parents[1]
        source_dir = repo_root / "digital_twin_package"
        vendored_dir = repo_root / "grid_map_digital_twin"
        mirrored_files = (
            "simulator.py",
            "sample_test.py",
            "package_metadata.json",
            "build_summary.json",
            "net_digital_twin.p",
        )

        for relative_path in mirrored_files:
            with self.subTest(path=relative_path):
                source_bytes = (source_dir / relative_path).read_bytes()
                vendored_bytes = (vendored_dir / relative_path).read_bytes()
                self.assertEqual(vendored_bytes, source_bytes)


if __name__ == "__main__":
    unittest.main()
