import io
import tempfile
import unittest
from pathlib import Path

from hil_scheduler import discover_config_profiles, select_startup_config_path


class HilSchedulerStartupConfigTests(unittest.TestCase):
    def _write_profile(self, root, name):
        path = Path(root) / name
        path.write_text("general: {}\n", encoding="utf-8")
        return path

    def test_explicit_config_argument_uses_repo_root_relative_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            expected = self._write_profile(tmpdir, "config_remote.yaml")

            selected = select_startup_config_path(
                argv=["--config", "config_remote.yaml"],
                project_root=tmpdir,
                stdin_isatty=False,
            )

            self.assertEqual(selected, expected.resolve())

    def test_single_discovered_profile_is_selected_automatically(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            expected = self._write_profile(tmpdir, "config_lab.yaml")

            selected = select_startup_config_path(
                argv=[],
                project_root=tmpdir,
                stdin_isatty=False,
            )

            self.assertEqual(selected, expected.resolve())

    def test_discovery_ignores_nested_config_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            expected = self._write_profile(tmpdir, "config.yaml")
            nested_dir = Path(tmpdir) / "profiles"
            nested_dir.mkdir()
            self._write_profile(nested_dir, "config_remote.yaml")

            discovered = discover_config_profiles(tmpdir)

            self.assertEqual(discovered, [expected.resolve()])

    def test_multiple_profiles_prompt_and_default_marker_are_shown(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_profile(tmpdir, "config_remote.yaml")
            expected = self._write_profile(tmpdir, "config.yaml")
            output = io.StringIO()

            selected = select_startup_config_path(
                argv=[],
                project_root=tmpdir,
                stdin_isatty=True,
                input_fn=lambda prompt: "1",
                output_stream=output,
            )

            self.assertEqual(selected, expected.resolve())
            rendered = output.getvalue()
            self.assertIn("Multiple startup config profiles found:", rendered)
            self.assertIn("config.yaml [default]", rendered)
            self.assertIn("config_remote.yaml", rendered)

    def test_enter_selects_default_config_yaml_when_present(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            expected = self._write_profile(tmpdir, "config.yaml")
            self._write_profile(tmpdir, "config_remote.yaml")

            selected = select_startup_config_path(
                argv=[],
                project_root=tmpdir,
                stdin_isatty=True,
                input_fn=lambda prompt: "",
                output_stream=io.StringIO(),
            )

            self.assertEqual(selected, expected.resolve())

    def test_invalid_prompt_input_reprompts_until_selection_is_valid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_profile(tmpdir, "config.yaml")
            expected = self._write_profile(tmpdir, "config_remote.yaml")
            responses = iter(["x", "9", "2"])
            output = io.StringIO()

            selected = select_startup_config_path(
                argv=[],
                project_root=tmpdir,
                stdin_isatty=True,
                input_fn=lambda prompt: next(responses),
                output_stream=output,
            )

            self.assertEqual(selected, expected.resolve())
            rendered = output.getvalue()
            self.assertIn("Invalid selection 'x'.", rendered)
            self.assertIn("Invalid selection '9'.", rendered)

    def test_non_interactive_multiple_profiles_requires_explicit_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_profile(tmpdir, "config.yaml")
            self._write_profile(tmpdir, "config_remote.yaml")

            with self.assertRaises(RuntimeError) as ctx:
                select_startup_config_path(
                    argv=[],
                    project_root=tmpdir,
                    stdin_isatty=False,
                )

            self.assertIn("Pass --config <filename>", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
