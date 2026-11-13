from pathlib import Path
import json
import os
import shutil
from contextlib import redirect_stdout
from io import StringIO
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
import prepare_release
import publish_release
from prepare_release import read_current_versions, render_pkgbuild, resolve_target_version
from release_checks import app_id, aur_package_names, is_dev_version, repo_root, sibling_core_sha256, bundle_sha256, release_bundle
from update_readme import (
    DEFAULT_MODEL, LOCALIZED_READMES, aur_package_names as readme_aur_names,
    language_switcher_html, validate_readme_bytes,
)


class ReleaseSkillTest(unittest.TestCase):
    def make_checkout(self, workspace, project, version="0.1.0-dev.20261109.0"):
        checkout = workspace / project
        checkout.mkdir()
        (checkout / ".gitignore").write_text("build/\ntarget/\n")
        subprocess.run(["git", "init", "-q", checkout], check=True)
        if project == "remya-core":
            for relative in ("packages/remya_client", "packages/remya_ui"):
                package = checkout / relative
                package.mkdir(parents=True)
                (package / "pubspec.yaml").write_text(f"name: {package.name}\nversion: {version}\n")
            (checkout / "tool").mkdir()
            (checkout / "tool/remya_cli.dart").write_text("void main() {}\n")
        else:
            (checkout / "pubspec.yaml").write_text(f"name: {project.replace('-', '_')}\nversion: {version}\n")
        (checkout / "Cargo.toml").write_text(f'[workspace.package]\nversion = "{version}"\n')
        return checkout

    def test_projects_resolve_from_checkout_instead_of_skill_location(self):
        version = "0.1.0-dev.20261109.0"
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            for project in ("remya-core", "remya-settings", "remya-bar", "remya-launcher", "remya-screen-capture"):
                checkout = self.make_checkout(workspace, project, version)
                with patch("release_checks.Path.cwd", return_value=checkout):
                    self.assertEqual(repo_root(), checkout)
                self.assertEqual(app_id(checkout), project.replace("-", "_"))
                self.assertEqual(read_current_versions(checkout), (version,) * (3 if project == "remya-core" else 2))
                for suffix in ("", "-bin", "-git"):
                    if project == "remya-screen-capture" and suffix == "-git":
                        continue
                    pkgname = project + suffix
                    pkgbuild = render_pkgbuild(pkgname, version, "a" * 64, core_version=version)
                    self.assertIn(f"pkgname={pkgname}", pkgbuild)
                    self.assertNotIn("@CORE_VER@", pkgbuild)
                    if project != "remya-core":
                        self.assertIn("'remya-core'", pkgbuild)
                        if project != "remya-settings":
                            self.assertNotIn("'remya-settings'", pkgbuild)
                        if not suffix:
                            self.assertIn(f"_core_ver={version}", pkgbuild)
                            self.assertIn("remya-core/archive/refs/tags/v", pkgbuild)
                            with self.assertRaises(ValueError):
                                render_pkgbuild(pkgname, version, "a" * 64)
                    subprocess.run(["bash", "-n"], input=pkgbuild, text=True, check=True)

    def test_core_hash_covers_both_packages_for_every_dependent(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            core = self.make_checkout(workspace, "remya-core")
            for project in ("remya-settings", "remya-bar", "remya-launcher", "remya-screen-capture"):
                checkout = self.make_checkout(workspace, project)
                previous = sibling_core_sha256(checkout)
                for package in ("remya_client", "remya_ui"):
                    source = core / f"packages/{package}/source.dart"
                    source.write_text(project)
                    current = sibling_core_sha256(checkout)
                    self.assertNotEqual(previous, current)
                    previous = current
                (core / "README.md").write_text(project)
                self.assertEqual(previous, sibling_core_sha256(checkout))
            (core / "packages/remya_ui/pubspec.yaml").unlink()
            with self.assertRaisesRegex(RuntimeError, "Remya Core"):
                sibling_core_sha256(checkout)

    def test_core_build_and_version_sync_use_rust_and_dart(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_checkout(Path(temporary), "remya-core")
            bundle = release_bundle(root)
            bundle.mkdir(parents=True)
            for binary in ("remyad", "remya-core-update", "remya"):
                (bundle / binary).write_text(binary)
            with patch.object(prepare_release, "run") as run:
                prepare_release.sync_version_files(root, "0.2.0")
                self.assertEqual(set(read_current_versions(root)), {"0.2.0"})
                self.assertEqual(prepare_release.build_release(root), bundle_sha256(bundle, "remya_core"))
                commands = [call.args[0] for call in run.call_args_list]
                self.assertIn(["cargo", "build", "--locked", "--release", "-p", "remya_service", "-p", "remya_updates"], commands)
                self.assertIn(["fvm", "dart", "compile", "exe", "tool/remya_cli.dart", "-o", "target/release/remya"], commands)
                self.assertFalse(any(command[:3] == ["fvm", "flutter", "build"] for command in commands))
            before = bundle_sha256(bundle, "remya_core")
            (bundle / "unrelated-build-file").write_text("ignored")
            self.assertEqual(before, bundle_sha256(bundle, "remya_core"))
            (bundle / "remya-core-update").unlink()
            with self.assertRaisesRegex(RuntimeError, "Incomplete"):
                bundle_sha256(bundle, "remya_core")

    def populate_bundle(self, root):
        project = root.name
        name = project.replace("-", "_")
        files = ["tool/install.py", "LICENSE", "README.md", "assets/licenses/rust-dependencies.txt"]
        bundle = release_bundle(root)
        bundle.mkdir(parents=True)
        if project == "remya-core":
            files += ["tool/install_system_updater.py", "packaging/top.remya.Core1.service"]
            for binary in ("remyad", "remya-core-update", "remya"):
                (bundle / binary).write_text(binary)
            (bundle / "unrelated-build-file").write_text("must not be archived")
        else:
            files += [f"linux/packaging/top.remya.{name.removeprefix('remya_')}.desktop", "assets/icons/app_icon.svg"]
            for relative in (name, "lib/libapp.so", "data/flutter_assets/AssetManifest.bin"):
                target = bundle / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(relative)
            if project == "remya-settings":
                files += ["linux/packaging/top.remya.settings.appimage.desktop", "assets/licenses/croppy-kiwi.txt"]
            else:
                files += [f"linux/packaging/{project}.json"]
            if project == "remya-bar":
                files += ["build/linux/x64/release/bundle/share/licenses/virtual-keyboard-unstable-v1.xml"]
            if project == "remya-launcher":
                files += ["build/linux/x64/release/bundle/remya-launcher-clipboard", "build/linux/x64/release/bundle/data/licenses/clipboard-rust-notices.txt"]
            if project == "remya-screen-capture":
                files += ["assets/licenses/material-symbols.txt", "lib/src/rust/capture/THIRD_PARTY_NOTICES.txt"]
        for relative in files:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("fixture\n")
        if project == "remya-core":
            (root / "packaging/top.remya.Core1.service").write_text(
                "[D-BUS Service]\nName=top.remya.Core1\nExec=@REMYAD@\nSystemdService=top.remya.core.service\n")
        elif project != "remya-settings":
            (root / f"linux/packaging/top.remya.{name.removeprefix('remya_')}.desktop").write_text(
                f"[Desktop Entry]\nExec={name}\nActions=settings;\n\n[Desktop Action settings]\nName=Settings\nExec={name} --settings\n")

    @unittest.skipUnless(shutil.which("makepkg") and shutil.which("zstd"), "Arch packaging tools required")
    def test_fixture_archives_packages_and_candidate_guards(self):
        version = "0.2.0"
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            core = self.make_checkout(workspace, "remya-core", version)
            for project in ("remya-core", "remya-settings", "remya-bar", "remya-launcher", "remya-screen-capture"):
                with self.subTest(project=project):
                    root = core if project == "remya-core" else self.make_checkout(workspace, project, version)
                    self.populate_bundle(root)
                    name = app_id(root)
                    manifest = dict(app_id=name, version=version, bundle_sha256=bundle_sha256(release_bundle(root), name),
                                    source_sha256=prepare_release.source_sha256(root),
                                    candidate_source_sha256=prepare_release.candidate_source_sha256(root),
                                    skill_sha256=prepare_release.skill_sha256())
                    if project != "remya-core":
                        manifest.update(core_version=version, core_packages_sha256=sibling_core_sha256(root))
                    # Stage fixture bytes only; never build, install, or publish an application.
                    with patch.object(prepare_release, "install_staged_release") as install:
                        release = prepare_release.stage_release_artifacts(root, version, manifest)
                        install.assert_not_called()
                    installed = workspace / "installed" / project
                    installed.parent.mkdir(exist_ok=True)
                    if project == "remya-core":
                        installed.mkdir()
                        for binary in ("remyad", "remya-core-update", "remya"):
                            shutil.copy2(release_bundle(root) / binary, installed / binary)
                    else:
                        shutil.copytree(release_bundle(root), installed)
                    with patch.object(publish_release, "installed_bundle", return_value=installed):
                        publish_release.verify_prepared_state(root, version)
                    extracted = workspace / "extracted" / project
                    extracted.mkdir(parents=True)
                    subprocess.run(["tar", "--zstd", "-xf", str(release / f"{name}-{version}-linux-x86_64.tar.zst"), "-C", str(extracted)], check=True)
                    archive_root = extracted / f"{name}-{version}-linux-x86_64"
                    self.assertFalse((archive_root / "target/release/unrelated-build-file").exists())
                    for suffix in ("", "-bin", "-git"):
                        if project == "remya-screen-capture" and suffix == "-git":
                            continue
                        pkgname = project + suffix
                        recipe = workspace / "PKGBUILD"
                        recipe.write_text(render_pkgbuild(pkgname, version, "a" * 64, core_version=version))
                        src = workspace / f"sources-{pkgname}"
                        src.mkdir()
                        source_root = src / (f"{name}-{version}-linux-x86_64" if suffix == "-bin" else project + (f"-{version}" if not suffix else ""))
                        shutil.copytree(archive_root, source_root)
                        if project != "remya-core" and not suffix:
                            (src / f"remya-core-{version}").mkdir()
                        destination = workspace / f"package-{pkgname}"
                        destination.mkdir()
                        environment = dict(os.environ, srcdir=str(src), pkgdir=str(destination), RECIPE=str(recipe))
                        subprocess.run(["bash", "-ec", 'source "$RECIPE"; if declare -F prepare >/dev/null; then prepare; fi; package'], env=environment, check=True)
                        if project == "remya-core":
                            self.assertEqual((destination / "usr/bin/remya").readlink(), Path("/usr/lib/remya-core/remya"))
                            self.assertIn("Exec=/usr/lib/remya-core/remyad", (destination / "usr/share/dbus-1/services/top.remya.Core1.service").read_text())
                            self.assertTrue((destination / "usr/lib/systemd/user/top.remya.core.service").is_file())
                        elif project == "remya-settings":
                            self.assertFalse((destination / "usr/lib/systemd").exists())
                            self.assertFalse((destination / "usr/share/dbus-1").exists())
                            self.assertFalse((destination / "usr/lib/remya_settings/libexec").exists())
                        else:
                            self.assertTrue((destination / f"usr/share/remya/apps/{project}.json").is_file())
                            self.assertIn(f"Exec={name} --settings", (destination / f"usr/share/applications/top.remya.{name.removeprefix('remya_')}.desktop").read_text())
                            if project in {"remya-bar", "remya-launcher"}:
                                unit = "top.remya.bar.service" if project == "remya-bar" else "top.remya.launcher.clipboard.service"
                                text = (destination / "usr/lib/systemd/user" / unit).read_text()
                                self.assertIn("Wants=top.remya.core.service", text)
                                self.assertIn("After=top.remya.core.service", text)
                                if project == "remya-launcher":
                                    self.assertIn("UMask=0077\nLimitCORE=0", text)
                                    self.assertTrue((destination / "usr/lib/remya_launcher/remya-launcher-clipboard").is_file())
                    if project != "remya-core":
                        changed = core / "packages/remya_ui/changed.dart"
                        changed.write_text(project)
                        with patch.object(publish_release, "installed_bundle", return_value=installed):
                            with self.assertRaisesRegex(RuntimeError, "Core packages changed"):
                                publish_release.verify_prepared_state(root, version)
                        with self.assertRaisesRegex(RuntimeError, "Core packages changed"):
                            prepare_release.refresh_readme(root, version)
                        changed.unlink()

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

    def test_only_dev_builds_are_github_prereleases(self):
        for version, prerelease in (("0.1.0", False), ("0.1.0-dev.20261113.0", True)):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as temporary:
                checkout = Path(temporary)
                (checkout / "pubspec.yaml").write_text("name: remya_settings\n")
                output = StringIO()
                with (
                    patch.object(sys, "argv", ["publish_release.py", "--version", version, "--dry-run"]),
                    patch.object(publish_release, "repo_root", return_value=checkout),
                    patch.object(publish_release, "verify_prepared_state",
                                 return_value=(checkout / "archive.tar.zst", {"base_commit": "HEAD"})),
                    patch.object(publish_release, "git", return_value=""),
                    patch.object(publish_release.subprocess, "run") as run,
                    redirect_stdout(output),
                ):
                    self.assertEqual(publish_release.main(), 0)
                    self.assertEqual(run.call_count, 1)
                command = next(line for line in output.getvalue().splitlines() if "gh release create" in line)
                self.assertEqual("--prerelease" in command.split(), prerelease)

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
            with self.assertRaisesRegex(RuntimeError, "Remya application checkout"):
                app_id(checkout)


if __name__ == "__main__":
    unittest.main()
