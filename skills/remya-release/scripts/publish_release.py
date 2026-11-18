#!/usr/bin/env python3
"""Publish the exact workstation-reviewed archive from a clean, pushed main."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
from prepare_release import read_current_versions, render_pkgbuild
from publish_aur import prepare_packages, publish_packages, verify_core_tag
from release_checks import (
    VERSION, app_id, aur_package_names, bundle_sha256, git, github_repo, is_dev_version,
    installed_bundle, network, release_bundle, remote_ref, repo_root, sha256_file, sibling_core_sha256,
    skill_root, skill_sha256, source_sha256,
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
        script = skill_root() / f"resources/{project}.install"
        expected_files = {"PKGBUILD", ".SRCINFO"}
        if script.is_file():
            expected_files.add(script.name)
        if {path.name for path in aur_dir.iterdir()} != expected_files:
            raise RuntimeError(f"Unexpected files in the staged AUR recipe for {aur_pkg}")
        if script.is_file() and (aur_dir / script.name).read_bytes() != script.read_bytes():
            raise RuntimeError(f"Staged {script.name} for {aur_pkg} does not match the release skill")
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


def release_info(root: Path, tag: str) -> dict | None:
    response = network([
        "gh", "release", "view", tag, "--repo", github_repo(root),
        "--json", "tagName,isDraft,isPrerelease,assets,body,url",
    ], root, missing="release not found")
    return json.loads(response) if response is not None else None


def verify_assets(root: Path, tag: str, files: list[Path], directory: Path) -> None:
    if not files:
        return
    command = ["gh", "release", "download", tag, "--repo", github_repo(root), "--dir", str(directory), "--clobber"]
    for path in files:
        command.extend(["--pattern", path.name])
    network(command, root)
    for path in files:
        if sha256_file(directory / path.name) != sha256_file(path):
            raise RuntimeError(f"Downloaded GitHub asset differs from the approved file: {path.name}")


def publish_github(root: Path, tag: str, archive: Path, create: list[str], directory: Path,
                   notes_file: Path | None) -> None:
    release = release_info(root, tag)
    if release is None:
        # gh can leave a draft if the asset upload was interrupted.
        result = subprocess.run(create, cwd=root)
        release = release_info(root, tag)
        if release is None:
            result.check_returncode()
    if release is None or release["tagName"] != tag or release["isPrerelease"] != is_dev_version(tag):
        raise RuntimeError(f"GitHub Release channel or tag does not match {tag}")
    if notes_file and release["body"].strip() != notes_file.read_text().strip():
        raise RuntimeError(f"GitHub Release notes differ from the approved notes for {tag}")

    files = [archive, archive.with_name("SHA256SUMS")]
    if release["isDraft"]:
        names = {asset["name"] for asset in release["assets"]}
        verify_assets(root, tag, [path for path in files if path.name in names], directory)
        missing = [str(path) for path in files if path.name not in names]
        if missing:
            subprocess.run(["gh", "release", "upload", tag, "--repo", github_repo(root), *missing], cwd=root, check=True)
        verify_assets(root, tag, files, directory)
        subprocess.run(["gh", "release", "edit", tag, "--repo", github_repo(root), "--draft=false"], cwd=root, check=True)
        release = release_info(root, tag)
        if not release or release["isDraft"]:
            raise RuntimeError(f"GitHub Release is still unpublished for {tag}")
    verify_assets(root, tag, files, directory)
    print(f"GitHub verified {tag}: {release['url']}", flush=True)


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
    parser.add_argument("--aur-maintainer", help="Also publish the release's AUR packages as this expected maintainer")
    parser.add_argument("--create-aur-package", action="append", default=[], metavar="PACKAGE")
    parser.add_argument("--allow-aur-downgrade", action="append", default=[], metavar="PACKAGE")
    args = parser.parse_args()
    if not args.dry_run and not args.confirm_user_approved:
        parser.error("Publication requires both approvals and --confirm-user-approved")
    if (args.create_aur_package or args.allow_aur_downgrade) and not args.aur_maintainer:
        parser.error("AUR overrides require --aur-maintainer")

    root = repo_root()
    archive, manifest = verify_prepared_state(root, args.version)
    if set(args.create_aur_package + args.allow_aur_downgrade) - set(manifest.get("aur_packages", [])):
        parser.error("AUR overrides must name a package in this release")
    if not args.dry_run and not args.skip_readme and not manifest.get("readme_refreshed"):
        raise RuntimeError("Complete the README review and refresh the approved archive before publishing")
    tag = f"v{args.version}"
    tag_ref = f"refs/tags/{tag}"
    head = git(root, "rev-parse", "HEAD")
    subprocess.run(["git", "merge-base", "--is-ancestor", manifest["base_commit"], head], cwd=root, check=True)

    remote = "origin"
    if not args.dry_run:
        if git(root, "status", "--porcelain"):
            raise RuntimeError("Commit the reviewed changes and use a clean worktree")
        target = github_repo(root)
        remote = git(root, "remote", "get-url", "--push", "origin")
        if remote.removesuffix(".git") not in {
            f"https://github.com/{target}",
            f"git@github.com:{target}",
            f"ssh://git@github.com/{target}",
        }:
            raise RuntimeError(f"origin must push to the Remya release repository {target}")
        remote_main = remote_ref(root, remote, "refs/heads/main")
        if head != remote_main or head != git(root, "rev-parse", "refs/heads/main"):
            raise RuntimeError("HEAD must match both local and remote main")
    local_tag = git(root, "tag", "--list", tag)
    if local_tag and git(root, "rev-parse", f"{tag_ref}^{{commit}}") != head:
        raise RuntimeError(f"Existing tag {tag} points to a different commit")

    create_tag = ["git", "tag", "-a", tag, "-m", f"Release {tag}"]
    push_tag = ["git", "push", "origin", f"{tag_ref}:{tag_ref}"]
    release = [
        "gh", "release", "create", tag, "--repo", github_repo(root), "--verify-tag",
        "--title", tag, str(archive), str(archive.with_name("SHA256SUMS")),
    ]
    if is_dev_version(args.version):
        release.append("--prerelease")
    notes_file = args.notes_file.resolve(strict=True) if args.notes_file else None
    if notes_file:
        release.extend(["--notes-file", str(notes_file)])
    else:
        release.append("--generate-notes")
    if args.dry_run:
        for command in ([create_tag] if not local_tag else []) + [push_tag, release]:
            print(f"==> $ {shlex.join(command)}", flush=True)
        print(f"AUR targets: {', '.join(manifest.get('aur_packages', []))}")
        return 0

    with tempfile.TemporaryDirectory(prefix="remya-publish-") as temporary:
        directory = Path(temporary)
        packages = []
        if args.aur_maintainer:
            if app_id(root) != "remya_core":
                verify_core_tag(root, manifest["core_version"])
            packages = prepare_packages(
                root, archive.parent, args.version, head, directory, maintainer=args.aur_maintainer,
                create=set(args.create_aur_package), allow_downgrade=set(args.allow_aur_downgrade),
            )
        published = remote_ref(root, remote, tag_ref)
        if published and published != head:
            raise RuntimeError(f"Remote tag {tag} points to a different commit")
        if not published:
            if not local_tag:
                subprocess.run(create_tag, cwd=root, check=True)
            network(push_tag, root)
            if remote_ref(root, remote, tag_ref) != head:
                raise RuntimeError(f"Remote tag {tag} does not match {head}")
        publish_github(root, tag, archive, release, directory, notes_file)
        publish_packages(packages, directory / archive.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
