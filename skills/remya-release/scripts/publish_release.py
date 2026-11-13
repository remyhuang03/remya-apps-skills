#!/usr/bin/env python3
"""Publish the exact workstation-reviewed archive from a clean, pushed main."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys

sys.dont_write_bytecode = True
from prepare_release import read_current_versions, render_pkgbuild
from release_checks import (
    VERSION, app_id, aur_package_names, bundle_sha256, git, github_repo, is_dev_version,
    installed_bundle, release_bundle, repo_root, sha256_file, sibling_core_sha256,
    skill_sha256, source_sha256,
)


def verify_prepared_state(root: Path, version: str) -> tuple[Path, dict]:
    if not VERSION.fullmatch(version) or set(read_current_versions(root)) != {version}:
        raise RuntimeError(f"Version files do not match release {version!r}")

    release_dir = root / "build/release" / version
    manifest = json.loads((release_dir / "manifest.json").read_text())
    archive = release_dir / f"{app_id(root)}-{version}-linux-x86_64.tar.zst"
    if (manifest["app_id"], manifest["version"], manifest["archive_name"]) != (app_id(root), version, archive.name):
        raise RuntimeError("Release manifest does not match the requested project and version")

    archive_hash = sha256_file(archive)
    if (
        archive_hash != manifest["archive_sha256"]
        or (release_dir / "SHA256SUMS").read_text() != f"{archive_hash}  {archive.name}\n"
    ):
        raise RuntimeError("Staged archive or SHA256SUMS changed after preparation")

    project = app_id(root).replace("_", "-")
    if (release_dir / "PKGBUILD").read_text() != render_pkgbuild(f"{project}-bin", version, archive_hash):
        raise RuntimeError("Staged PKGBUILD does not match the prepared archive")
    srcinfo = subprocess.check_output(["makepkg", "--printsrcinfo"], cwd=release_dir, text=True)
    if (release_dir / ".SRCINFO").read_text() != srcinfo:
        raise RuntimeError("Staged .SRCINFO does not match PKGBUILD")

    for aur_pkg in aur_package_names(project, version):
        aur_dir = release_dir / "aur" / aur_pkg
        expected = render_pkgbuild(
            aur_pkg, version, archive_hash, core_version=manifest.get("core_version", "")
        )
        if (aur_dir / "PKGBUILD").read_text() != expected:
            raise RuntimeError(f"Staged AUR PKGBUILD for {aur_pkg} does not match the prepared release")
        aur_srcinfo = subprocess.check_output(["makepkg", "--printsrcinfo"], cwd=aur_dir, text=True)
        if (aur_dir / ".SRCINFO").read_text() != aur_srcinfo:
            raise RuntimeError(f"Staged AUR .SRCINFO for {aur_pkg} does not match PKGBUILD")

    if source_sha256(root) != manifest["source_sha256"]:
        raise RuntimeError("Source changed after preparation. Rebuild and review it before publishing.")

    if manifest.get("skill_sha256") != skill_sha256():
        raise RuntimeError("The release skill changed after preparation")
    if app_id(root) != "remya_core" and manifest.get("core_packages_sha256") != sibling_core_sha256(root):
        raise RuntimeError("The sibling Core packages changed after preparation")

    for bundle in (release_bundle(root), installed_bundle(app_id(root))):
        if bundle_sha256(bundle, app_id(root)) != manifest["bundle_sha256"]:
            raise RuntimeError(f"Bundle changed after preparation: {bundle}")
    return archive, manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="Release version prepared in Phase 1")
    parser.add_argument(
        "--confirm-user-approved", action="store_true",
        help="Confirm explicit approval of the installed candidate and publication",
    )
    parser.add_argument("--notes-file", type=Path, help="Reviewed GitHub release notes")
    parser.add_argument("--dry-run", action="store_true", help="Check prepared artifacts and print commands")
    parser.add_argument("--skip-readme", action="store_true", help="Use only when the user has waived the README workflow")
    args = parser.parse_args()
    if not args.dry_run and not args.confirm_user_approved:
        parser.error("Publication requires both approvals and --confirm-user-approved")

    root = repo_root()
    archive, manifest = verify_prepared_state(root, args.version)
    if not args.dry_run and not args.skip_readme and not manifest.get("readme_refreshed"):
        raise RuntimeError("Complete the README review and refresh the approved archive before publishing")
    tag = f"v{args.version}"
    tag_ref = f"refs/tags/{tag}"
    head = git(root, "rev-parse", "HEAD")
    subprocess.run(["git", "merge-base", "--is-ancestor", manifest["base_commit"], head], cwd=root, check=True)

    commands = []
    if not args.dry_run:
        if git(root, "status", "--porcelain"):
            raise RuntimeError("Commit the reviewed changes and use a clean worktree")
        target = github_repo(root)
        push_url = git(root, "remote", "get-url", "--push", "origin").removesuffix(".git")
        if push_url not in {
            f"https://github.com/{target}",
            f"git@github.com:{target}",
            f"ssh://git@github.com/{target}",
        }:
            raise RuntimeError(f"origin must push to the Remya release repository {target}")
        remote_main = git(root, "ls-remote", "--exit-code", "origin", "refs/heads/main").split()[0]
        if head != remote_main or head != git(root, "rev-parse", "refs/heads/main"):
            raise RuntimeError("HEAD must match both local and remote main")
    local_tag = git(root, "tag", "--list", tag)
    if local_tag:
        if git(root, "rev-parse", f"{tag_ref}^{{commit}}") != head:
            raise RuntimeError(f"Existing tag {tag} points to a different commit")
    else:
        commands.append(["git", "tag", "-a", tag, "-m", f"Release {tag}"])

    commands.append(["git", "push", "origin", f"{tag_ref}:{tag_ref}"])
    release = [
        "gh", "release", "create", tag, "--repo", github_repo(root), "--verify-tag",
        "--title", tag, str(archive), str(archive.with_name("SHA256SUMS")),
    ]
    if is_dev_version(args.version):
        release.append("--prerelease")
    if args.notes_file:
        release.extend(["--notes-file", str(args.notes_file.resolve(strict=True))])
    else:
        release.append("--generate-notes")
    commands.append(release)

    for command in commands:
        print(f"==> $ {shlex.join(command)}", flush=True)
        if not args.dry_run:
            subprocess.run(command, cwd=root, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
