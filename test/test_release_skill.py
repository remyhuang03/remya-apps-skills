from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parents[1] / "skills/remya-release/scripts"
sys.path.insert(0, str(SCRIPTS))
from prepare_release import read_current_versions, render_pkgbuild
from release_checks import app_id, repo_root, sibling_client_sha256


class ReleaseSkillTest(unittest.TestCase):
    def test_both_projects_resolve_from_checkout_instead_of_skill_location(self):
        version = "0.1.0-dev.20261109.0"
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            for project in ("remya-settings", "remya-bar"):
                checkout = workspace / project
                checkout.mkdir()
                subprocess.run(["git", "init", "-q", checkout], check=True)
                name = project.replace("-", "_")
                pubspec = checkout / "pubspec.yaml"
                pubspec.write_text(f"name: {name}\nversion: {version}\n")
                (checkout / "Cargo.toml").write_text(f'[workspace.package]\nversion = "{version}"\n')
                if project == "remya-settings":
                    client = checkout / "packages/remya_settings_client/pubspec.yaml"
                    client.parent.mkdir(parents=True)
                    client.write_text(f"name: remya_settings_client\nversion: {version}\n")

                with patch("release_checks.Path.cwd", return_value=checkout):
                    self.assertEqual(repo_root(), checkout)
                self.assertEqual(app_id(checkout), name)
                self.assertEqual(read_current_versions(checkout), (version,) * (3 if project == "remya-settings" else 2))
                self.assertIn(f"pkgname={project}-bin", render_pkgbuild(checkout, version, "a" * 64))
                if project == "remya-bar":
                    self.assertTrue(sibling_client_sha256(checkout))

    def test_other_app_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary)
            (checkout / "pubspec.yaml").write_text("name: another_app\n")
            with self.assertRaisesRegex(RuntimeError, "Remya Bar or Remya Settings"):
                app_id(checkout)


if __name__ == "__main__":
    unittest.main()
