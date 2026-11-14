#!/usr/bin/env python3
"""Prepare a Remya application release or build a headless CI artifact.

Synchronizes the dev/release version in the application manifests, regenerates
third-party Rust notices, runs the validation suite, builds the release bundle,
installs it on the workstation via tool/install.py, and stages the GitHub
Release archive + AUR PKGBUILD/.SRCINFO under build/release/<version>/.

After candidate approval, --refresh-readme repackages the same bundle.
Does NOT commit, tag, push, or publish.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
from release_checks import (
    VERSION, app_id, aur_package_names, bundle_sha256, git, installed_bundle,
    is_dev_version, release_bundle, repo_root, sha256_file, sibling_core_sha256, skill_root,
    skill_sha256, source_sha256,
)

DEV_VERSION_RE = re.compile(r"^(\d+\.\d+\.\d+)-dev\.(\d{8})\.(\d+)$")


def run(cmd: list[str], *, cwd: Path, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess[str]:
    print(f"==> $ {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, cwd=cwd, check=check, text=True, capture_output=capture)


def pubspec_paths(root: Path) -> tuple[str, ...]:
    if app_id(root) == "remya_core":
        return ("packages/remya_client/pubspec.yaml", "packages/remya_ui/pubspec.yaml")
    return ("pubspec.yaml",)


def read_current_versions(root: Path) -> tuple[str, ...]:
    versions = []
    for relative in pubspec_paths(root):
        match = re.search(r"^version:[ \t]*(\S+)[ \t]*$", (root / relative).read_text(), re.MULTILINE)
        if not match:
            raise RuntimeError(f"Unable to parse the version in {relative}")
        versions.append(match.group(1))
    cargo = (root / "Cargo.toml").read_text()
    match = re.search(r'^version[ \t]*=[ \t]*"([^"]+)"[ \t]*$', cargo, re.MULTILINE)
    if not match:
        raise RuntimeError("Unable to parse the version in Cargo.toml")
    return (*versions, match.group(1))


def existing_tag_sequences(root: Path, base: str, date_str: str) -> list[int]:
    prefix = f"v{base}-dev.{date_str}."
    result = subprocess.run(
        ["git", "tag", "--list", f"{prefix}*"],
        cwd=root,
        check=True,
        text=True,
        capture_output=True,
    )
    sequences: list[int] = []
    for line in result.stdout.splitlines():
        tag = line.strip()
        if tag.startswith(prefix):
            tail = tag[len(prefix):]
            if tail.isdecimal():
                sequences.append(int(tail))
    return sorted(sequences)


def resolve_target_version(root: Path, requested: str | None, *, bump: bool, allow_stable: bool) -> str:
    pub_ver, *_, cargo_ver = read_current_versions(root)
    if requested:
        if VERSION.fullmatch(requested) and (is_dev_version(requested) or allow_stable):
            return requested
        raise ValueError(
            f"Invalid version {requested!r}: expected <major>.<minor>.<patch>-dev.<YYYYMMDD>.<seq>, "
            "or pass --allow-stable for X.Y.Z (alpha/beta/stable) releases"
        )

    base_match = re.match(r"^(\d+\.\d+\.\d+)", cargo_ver) or re.match(r"^(\d+\.\d+\.\d+)", pub_ver)
    base = base_match.group(1) if base_match else "0.1.0"
    today = datetime.now().strftime("%Y%m%d")
    tag_seqs = existing_tag_sequences(root, base, today)

    current_match = DEV_VERSION_RE.match(pub_ver)
    if current_match and current_match.group(1) == base and current_match.group(2) == today:
        current_seq = int(current_match.group(3))
        if not bump and current_seq not in tag_seqs:
            return f"{base}-dev.{today}.{current_seq}"
        next_seq = max([current_seq, *tag_seqs]) + 1
        return f"{base}-dev.{today}.{next_seq}"

    next_seq = (max(tag_seqs) + 1) if tag_seqs else 0
    return f"{base}-dev.{today}.{next_seq}"


def sync_version_files(root: Path, version: str) -> None:
    for rel_path in pubspec_paths(root):
        path = root / rel_path
        text = path.read_text()
        updated, count = re.subn(
            r"^version:[^\S\n]*\S+[^\S\n]*$",
            f"version: {version}",
            text,
            count=1,
            flags=re.MULTILINE,
        )
        if count != 1:
            raise RuntimeError(f"Failed to update version in {rel_path}")
        path.write_text(updated)

    cargo_path = root / "Cargo.toml"
    cargo_text = cargo_path.read_text()
    new_cargo, cargo_count = re.subn(
        r'^version[ \t]*=[ \t]*"[^"]+"[ \t]*$',
        f'version = "{version}"',
        cargo_text,
        count=1,
        flags=re.MULTILINE,
    )
    if cargo_count != 1:
        raise RuntimeError("Failed to update version in Cargo.toml")
    cargo_path.write_text(new_cargo)

    fetch_dart_dependencies(root)
    run(["cargo", "metadata", "--format-version", "1", "--filter-platform", "x86_64-unknown-linux-gnu"], cwd=root, capture=True)
    if app_id(root) == "remya_screen_capture":
        notices = (root / "lib/src/rust/capture/THIRD_PARTY_NOTICES.txt").read_text()
        if f"Cargo.lock SHA-256: {sha256_file(root / 'Cargo.lock')}" not in notices.splitlines():
            raise RuntimeError("Update the Screen Capture Rust notices for the current Cargo.lock")
    elif app_id(root) != "remya_launcher":
        run([sys.executable, "tool/generate_rust_notices.py"], cwd=root)


def fetch_dart_dependencies(root: Path, *, locked: bool = False) -> None:
    suffix = ["--enforce-lockfile"] if locked else []
    if app_id(root) == "remya_core":
        for relative, command in (("packages/remya_client", "dart"), ("packages/remya_ui", "flutter"), ("tool", "dart")):
            run(["fvm", command, "pub", "get", *suffix], cwd=root / relative)
    else:
        run(["fvm", "flutter", "pub", "get", *suffix], cwd=root)


def run_validation(root: Path, *, skip_desktop_smoke: bool) -> None:
    core = app_id(root) == "remya_core"
    if core:
        run(["fvm", "dart", "format", "--output=none", "--set-exit-if-changed", "packages", "tool"], cwd=root)
        for relative, command in (("packages/remya_client", "dart"), ("packages/remya_ui", "flutter"), ("tool", "dart")):
            run(["fvm", command, "analyze"], cwd=root / relative)
    else:
        run(["fvm", "dart", "format", "--output=none", "--set-exit-if-changed", "lib", "test"], cwd=root)
        run(["fvm", "flutter", "analyze"], cwd=root)
        run(["fvm", "flutter", "test"], cwd=root)
    if app_id(root) == "remya_settings":
        run([sys.executable, "-m", "unittest", "test/xdg_install_test.py"], cwd=root)
    if app_id(root) == "remya_launcher":
        run([sys.executable, "-m", "unittest", "tool/test_install.py"], cwd=root)
    run(["cargo", "fmt", "--all", "--", "--check"], cwd=root)
    clippy = ["cargo", "clippy", "--locked", "--workspace", "--all-targets"]
    run(clippy if app_id(root) == "remya_settings" else [*clippy, "--", "-D", "warnings"], cwd=root)
    run(["cargo", "test", "--locked", "--workspace"], cwd=root)
    run(["git", "diff", "--check"], cwd=root)

    if app_id(root) in {"remya_bar", "remya_launcher", "remya_screen_capture"} and not skip_desktop_smoke:
        run(["fvm", "flutter", "build", "linux", "--debug"], cwd=root)
        if app_id(root) != "remya_bar":
            return
        run(["cmake", "--build", "build/linux/x64/debug", "--target", "bar_lifecycle_test"], cwd=root)
        run(["build/linux/x64/debug/bundle/bar_lifecycle_test"], cwd=root)
        if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
            run([sys.executable, "test/bar_window_smoke.py"], cwd=root)
        else:
            print("Skipping Hyprland window smoke (no active Hyprland session)")


def invalidate_icon_stamps(root: Path) -> None:
    flutter_build_dir = root / ".dart_tool/flutter_build"
    if not flutter_build_dir.is_dir():
        return
    for stamp in flutter_build_dir.glob("*/release_bundle_linux-x64_assets.stamp"):
        stamp.unlink(missing_ok=True)


def build_release(root: Path) -> str:
    if app_id(root) == "remya_settings":
        invalidate_icon_stamps(root)
    if app_id(root) == "remya_core":
        run(["cargo", "build", "--locked", "--release", "-p", "remya_service", "-p", "remya_updates"], cwd=root)
        run(["fvm", "dart", "compile", "exe", "tool/remya_cli.dart", "-o", "target/release/remya"], cwd=root)
    else:
        run(["fvm", "flutter", "build", "linux", "--release"], cwd=root)
    return bundle_sha256(release_bundle(root), app_id(root))


def install_staged_release(root: Path, release_dir: Path, version: str, built_hash: str) -> None:
    stem = f"{app_id(root)}-{version}-linux-x86_64"
    with tempfile.TemporaryDirectory(prefix=f"{app_id(root)}-release-") as temporary:
        extracted = Path(temporary) / stem
        run(["tar", "--zstd", "-xf", str(release_dir / f"{stem}.tar.zst"), "-C", temporary], cwd=root)
        run([sys.executable, "tool/install.py"], cwd=extracted)

    if bundle_sha256(installed_bundle(app_id(root)), app_id(root)) != built_hash:
        raise RuntimeError("Installed workstation bundle differs from the built bundle")
    unit = f"top.{app_id(root).replace('_', '.')}.service"
    if app_id(root) == "remya_launcher":
        unit = "top.remya.launcher.clipboard.service"
    if app_id(root) in {"remya_core", "remya_bar", "remya_launcher"}:
        run(["systemctl", "--user", "is-active", "--quiet", unit], cwd=root)


def render_pkgbuild(pkgname: str, version: str, archive_hash: str, *, core_version: str = "") -> str:
    template = (skill_root() / f"resources/{pkgname}.PKGBUILD.in").read_text()
    if "@CORE_VER@" in template and not VERSION.fullmatch(core_version):
        raise ValueError("The source package requires a published Core version")
    return (
        template.replace("@UPSTREAM_VER@", version)
        .replace("@CORE_VER@", core_version)
        .replace("@PKGVER@", version.replace("-", "_"))
        .replace("@SHA256@", archive_hash)
    )


def copy_install_script(project: str, directory: Path) -> None:
    """Places the project's pacman install script next to its PKGBUILD, if it has one."""
    script = skill_root() / f"resources/{project}.install"
    if script.is_file():
        shutil.copyfile(script, directory / script.name)


def stage_release_artifacts(root: Path, version: str, manifest: dict, *, ci: bool = False) -> Path:
    release_dir = root / ("build/ci-release" if ci else "build/release") / version
    release_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=release_dir.parent) as temporary:
        staging = Path(temporary)
        write_release_artifacts(root, version, manifest, staging)
        for artifact in staging.iterdir():
            if artifact.name != "manifest.json":
                artifact.replace(release_dir / artifact.name)
        (staging / "manifest.json").replace(release_dir / "manifest.json")
    return release_dir


def write_release_artifacts(root: Path, version: str, manifest: dict, release_dir: Path) -> None:
    stem = f"{app_id(root)}-{version}-linux-x86_64"
    staging_root = release_dir / stem
    core = app_id(root) == "remya_core"
    bundle_dst = staging_root / ("target/release" if core else "build/linux/x64/release/bundle")
    bundle_dst.parent.mkdir(parents=True)
    if core:
        bundle_dst.mkdir()
        for binary in ("remyad", "remya-core-update", "remya"):
            shutil.copy2(release_bundle(root) / binary, bundle_dst / binary)
    else:
        shutil.copytree(release_bundle(root), bundle_dst, symlinks=True)

    common = ("tool/install.py", "LICENSE", "README.md")
    if app_id(root) not in {"remya_screen_capture", "remya_launcher"}:
        common += ("assets/licenses/rust-dependencies.txt",)
    if core:
        extras = ("packaging/top.remya.Core1.service", "packaging/50-top.remya.core.update.rules")
    else:
        package = "top.remya.screen_capture" if app_id(root) == "remya_screen_capture" else "top." + app_id(root).replace("_", ".")
        extras = (f"linux/packaging/{package}.desktop", "assets/icons/app_icon.svg")
        if app_id(root) == "remya_settings":
            extras += ("linux/packaging/top.remya.settings.appimage.desktop", "assets/licenses/croppy-kiwi.txt")
        else:
            extras += (f"linux/packaging/{app_id(root).replace('_', '-')}.json",)
        if app_id(root) == "remya_screen_capture":
            extras += ("assets/licenses/material-symbols.txt", "lib/src/rust/capture/THIRD_PARTY_NOTICES.txt")
    for relative in (*common, *extras):
        target = staging_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / relative, target)
    for localized_readme in sorted(root.glob("docs/README.*.md")):
        target = staging_root / localized_readme.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(localized_readme, target)

    archive_name = f"{stem}.tar.zst"
    archive_path = release_dir / archive_name
    # Checkouts on filesystems with synthetic modes can appear world-writable.
    run(
        ["tar", "--zstd", "--mode=go-w", "--owner=0", "--group=0", "-cf", str(archive_path), stem],
        cwd=release_dir,
    )
    shutil.rmtree(staging_root)

    archive_hash = sha256_file(archive_path)
    (release_dir / "SHA256SUMS").write_text(f"{archive_hash}  {archive_name}\n")

    project = app_id(root).replace("_", "-")
    (release_dir / "PKGBUILD").write_text(render_pkgbuild(f"{project}-bin", version, archive_hash))
    copy_install_script(project, release_dir)
    srcinfo = run(["makepkg", "--printsrcinfo"], cwd=release_dir, capture=True).stdout
    (release_dir / ".SRCINFO").write_text(srcinfo)

    aur_packages = aur_package_names(project, version)
    for aur_pkg in aur_packages:
        aur_dir = release_dir / "aur" / aur_pkg
        aur_dir.mkdir(parents=True, exist_ok=True)
        (aur_dir / "PKGBUILD").write_text(render_pkgbuild(
            aur_pkg, version, archive_hash, core_version=manifest.get("core_version", "")
        ))
        copy_install_script(project, aur_dir)
        aur_srcinfo = run(["makepkg", "--printsrcinfo"], cwd=aur_dir, capture=True).stdout
        (aur_dir / ".SRCINFO").write_text(aur_srcinfo)

    manifest = {
        **manifest,
        "archive_name": archive_name,
        "archive_sha256": archive_hash,
        "dev": is_dev_version(version),
        "aur_packages": list(aur_packages),
    }
    (release_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def candidate_source_sha256(root: Path) -> str:
    return source_sha256(root, ":(exclude,top)README.md", ":(exclude,glob)docs/README.*.md")


def refresh_readme(root: Path, version: str) -> Path:
    release_dir = root / "build/release" / version
    manifest = json.loads((release_dir / "manifest.json").read_text())
    if (manifest["app_id"], manifest["version"]) != (app_id(root), version):
        raise RuntimeError("Candidate manifest does not match this project and version")
    if manifest.get("candidate_source_sha256") != candidate_source_sha256(root):
        raise RuntimeError("Candidate sources changed beyond README files. Prepare and review a new candidate.")
    if manifest.get("skill_sha256") != skill_sha256():
        raise RuntimeError("The release skill changed. Prepare and review a new candidate.")
    if app_id(root) != "remya_core" and manifest.get("core_packages_sha256") != sibling_core_sha256(root):
        raise RuntimeError("The sibling Core packages changed. Prepare and review a new candidate.")
    for bundle in (release_bundle(root), installed_bundle(app_id(root))):
        if bundle_sha256(bundle, app_id(root)) != manifest["bundle_sha256"]:
            raise RuntimeError(f"Bundle changed after candidate preparation: {bundle}")
    archive = release_dir / f"{app_id(root)}-{version}-linux-x86_64.tar.zst"
    if sha256_file(archive) != manifest["archive_sha256"]:
        raise RuntimeError("Candidate archive changed after preparation")
    manifest.update(source_sha256=source_sha256(root), readme_refreshed=True)
    return stage_release_artifacts(root, version, manifest)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", help="Explicit release version (e.g. 0.1.0-dev.20261109.0)")
    parser.add_argument("--bump", action="store_true", help="Increment today's dev sequence number")
    parser.add_argument("--allow-stable", action="store_true", help="Allow stable X.Y.Z version format")
    parser.add_argument("--skip-version-sync", action="store_true", help="Keep current version files unchanged")
    parser.add_argument(
        "--refresh-readme",
        action="store_true",
        help="Restage the approved bundle with the updated README, without building or installing",
    )
    parser.add_argument("--skip-checks", action="store_true", help="Skip format/analyze/test validation suite")
    parser.add_argument("--skip-desktop-smoke", action="store_true", help="Skip the Bar desktop validation")
    parser.add_argument("--ci", action="store_true", help="Validate and package a committed build without installing")
    args = parser.parse_args()

    root = repo_root()
    if not shutil.which("makepkg"):
        raise RuntimeError("makepkg is required to stage AUR metadata")
    if args.refresh_readme and not args.version:
        parser.error("--refresh-readme requires --version")

    if args.ci and (args.refresh_readme or args.skip_checks):
        parser.error("--ci cannot be combined with --refresh-readme or --skip-checks")
    if args.skip_version_sync or args.refresh_readme or args.ci:
        versions = read_current_versions(root)
        version = versions[0]
        if set(versions) != {version}:
            raise RuntimeError(f"Version files differ: {versions}")
    else:
        version = resolve_target_version(root, args.version, bump=args.bump, allow_stable=args.allow_stable)
    if app_id(root) == "remya_screen_capture" and is_dev_version(version):
        raise ValueError("The release skill has no Screen Capture -git recipe")
    if not VERSION.fullmatch(version):
        raise ValueError(f"Invalid release version: {version!r}")
    if args.version and args.version != version:
        raise ValueError(f"Requested version {args.version} differs from {version}")
    if not args.ci and (git(root, "tag", "--list", f"v{version}") or git(
        root, "ls-remote", "origin", f"refs/tags/v{version}"
    )):
        raise RuntimeError(f"Release tag v{version} already exists")
    if args.refresh_readme:
        release_dir = refresh_readme(root, version)
        print(f"README refreshed in {release_dir}. Present the README diff for release approval.")
        return 0
    if args.ci:
        dirty = git(root, "status", "--porcelain")
        if dirty:
            raise RuntimeError(f"CI requires a clean source checkout:\n{dirty}")
        fetch_dart_dependencies(root, locked=True)
    elif not args.skip_version_sync:
        sync_version_files(root, version)

    if not args.skip_checks:
        run_validation(root, skip_desktop_smoke=args.ci or args.skip_desktop_smoke)

    source_hash = source_sha256(root)
    core_hash = sibling_core_sha256(root) if app_id(root) != "remya_core" else None
    bundle_hash = build_release(root)
    if source_sha256(root) != source_hash:
        raise RuntimeError("Source files changed during the release build")
    if core_hash is not None and sibling_core_sha256(root) != core_hash:
        raise RuntimeError("The sibling Core packages changed during the build")
    package = f"top.{app_id(root).replace('_', '.')}"
    manifest = {
        "app_id": app_id(root),
        "package": package,
        "version": version,
        "tag": f"v{version}",
        "pkgver": version.replace("-", "_"),
        "prerelease": is_dev_version(version),
        "bundle_sha256": bundle_hash,
        "source_sha256": source_hash,
        "candidate_source_sha256": candidate_source_sha256(root),
        "base_commit": git(root, "rev-parse", "HEAD"),
        "prepared_at": datetime.now().astimezone().isoformat(),
        "readme_refreshed": False,
        "skill_sha256": skill_sha256(),
    }
    if core_hash is not None:
        core_versions = read_current_versions(root.parent / "remya-core")
        if len(set(core_versions)) != 1 or not VERSION.fullmatch(core_versions[0]):
            raise RuntimeError("Core package and workspace release versions must match")
        manifest["core_version"] = core_versions[0]
        manifest["core_packages_sha256"] = core_hash
    release_dir = stage_release_artifacts(root, version, manifest, ci=args.ci)
    if args.ci:
        run(["sha256sum", "-c", "SHA256SUMS"], cwd=release_dir)
        run(["makepkg", "--nodeps", "--clean"], cwd=release_dir)
        print(f"CI package verified in {release_dir}")
        return 0
    install_staged_release(root, release_dir, version, bundle_hash)

    print("\n============================================================")
    print(f"Prepared {package} release v{version}")
    print(f"Workstation installed & verified: {installed_bundle(app_id(root))}")
    print(f"Staged artifacts directory:       {release_dir}")
    print(f"  - {release_dir / f'{app_id(root)}-{version}-linux-x86_64.tar.zst'}")
    print(f"  - {release_dir / 'SHA256SUMS'}")
    print(f"  - {release_dir / 'PKGBUILD'}")
    print(f"  - {release_dir / '.SRCINFO'}")
    for aur_pkg in aur_package_names(app_id(root).replace("_", "-"), version):
        print(f"  - {release_dir / 'aur' / aur_pkg / 'PKGBUILD'}")
        print(f"  - {release_dir / 'aur' / aur_pkg / '.SRCINFO'}")
    print("============================================================")
    print("STOP HERE: Present the installed candidate for approval.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
