from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
RELEASE_SCRIPTS = Path(__file__).resolve().parents[1] / "skills/remya-release/scripts"
README_SCRIPTS = Path(__file__).resolve().parents[1] / "skills/remya-readme/scripts"
sys.path.insert(0, str(RELEASE_SCRIPTS))
sys.path.insert(0, str(README_SCRIPTS))
from prepare_release import read_current_versions, render_pkgbuild, resolve_target_version
from release_checks import app_id, aur_package_names, is_dev_version, repo_root, sibling_client_sha256
from update_readme import (
    DEFAULT_MODEL, LOCALIZED_READMES, aur_package_names as readme_aur_names,
    language_switcher_html, validate_readme_bytes,
)


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
                for pkgname in (project, f"{project}-bin", f"{project}-git"):
                    pkgbuild = render_pkgbuild(pkgname, version, "a" * 64)
                    self.assertIn(f"pkgname={pkgname}", pkgbuild)
                    self.assertRegex(pkgbuild, r"(?m)^  'sqlite'$")
                if project == "remya-bar":
                    self.assertTrue(sibling_client_sha256(checkout))

    def test_aur_package_names_by_release_channel(self):
        self.assertTrue(is_dev_version("0.1.0-dev.20261109.0"))
        self.assertEqual(aur_package_names("remya-bar", "0.1.0-dev.20261109.0"), ("remya-bar-git",))
        self.assertFalse(is_dev_version("0.1.0"))
        self.assertEqual(aur_package_names("remya-bar", "0.1.0"), ("remya-bar", "remya-bar-bin"))
        self.assertEqual(aur_package_names("remya-settings", "0.1.0"), ("remya-settings", "remya-settings-bin"))
        self.assertEqual(readme_aur_names("remya-bar", "dev"), ("remya-bar-git",))
        for channel in ("alpha", "beta", "stable"):
            self.assertEqual(readme_aur_names("remya-bar", channel), ("remya-bar", "remya-bar-bin"))

    def test_resolve_target_version_rejects_non_dev_suffixes(self):
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary)
            (checkout / "pubspec.yaml").write_text("name: remya_bar\nversion: 0.1.0-dev.20261109.0\n")
            (checkout / "Cargo.toml").write_text('[workspace.package]\nversion = "0.1.0-dev.20261109.0"\n')
            for invalid in ("0.1.0-alpha.0", "0.1.0-beta.2"):
                with self.assertRaises(ValueError):
                    resolve_target_version(checkout, invalid, bump=False, allow_stable=True)
            with self.assertRaises(ValueError):
                resolve_target_version(checkout, "0.1.0", bump=False, allow_stable=False)
            self.assertEqual(resolve_target_version(checkout, "0.1.0", bump=False, allow_stable=True), "0.1.0")

    def test_readme_model_and_multilang_configuration(self):
        self.assertEqual(DEFAULT_MODEL, "gemini-4-hydrogen-medium")
        self.assertEqual(
            [code for code, _ in LOCALIZED_READMES],
            ["zh-CN", "zh-TW", "ru", "de", "es", "ja"],
        )
        en_switcher = language_switcher_html(active_code=None)
        self.assertIn("<strong>English</strong>", en_switcher)
        self.assertIn('<a href="docs/README.zh-CN.md">简体中文</a>', en_switcher)
        zh_switcher = language_switcher_html(active_code="zh-CN")
        self.assertIn('<a href="../README.md">English</a>', zh_switcher)
        self.assertIn("<strong>简体中文</strong>", zh_switcher)

        validate_readme_bytes(
            b'<p align="center"></p>\n<h1 align="center">App</h1>\n[License](LICENSE)\n',
            is_localized=False,
        )
        validate_readme_bytes(
            b'<p align="center"></p>\n<h1 align="center">App</h1>\n[License](../LICENSE)\n',
            is_localized=True,
        )
        with self.assertRaises(RuntimeError):
            validate_readme_bytes(b"# Plain heading\n(LICENSE)\n", is_localized=False)

    def test_other_app_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary)
            (checkout / "pubspec.yaml").write_text("name: another_app\n")
            with self.assertRaisesRegex(RuntimeError, "Remya Bar or Remya Settings"):
                app_id(checkout)


if __name__ == "__main__":
    unittest.main()
