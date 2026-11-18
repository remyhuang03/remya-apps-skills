"""Prepare and publish the channel-specific AUR recipes for a verified release."""
from dataclasses import dataclass
import json
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import urlencode

from release_checks import aur_package_names, git, github_repo, network, remote_ref

AUR = "https://aur.archlinux.org"


@dataclass
class Package:
    name: str
    recipe: Path
    checkout: Path
    previous_version: str
    version: str


def package_version(srcinfo: str) -> str:
    fields = dict(re.findall(r"^\s+(epoch|pkgver|pkgrel) = (\S+)$", srcinfo, re.MULTILINE))
    epoch = f"{fields['epoch']}:" if fields.get("epoch") not in (None, "0") else ""
    return f"{epoch}{fields['pkgver']}-{fields['pkgrel']}"


def verify_core_tag(root: Path, core_version: str) -> None:
    core = root.parent / "remya-core"
    ref = f"refs/tags/v{core_version}"
    commit = remote_ref(core, "https://github.com/remyhuang03/remya-core.git", ref)
    if not commit:
        raise RuntimeError(f"Publish the sibling Core tag {ref} first")
    if git(core, "status", "--porcelain"):
        raise RuntimeError("The sibling Core checkout must be clean before AUR publication")
    subprocess.run(
        ["git", "diff", "--exit-code", commit, "--", "packages/remya_client", "packages/remya_ui"],
        cwd=core, check=True,
    )


def prepare_packages(
    root: Path, release_dir: Path, version: str, head: str, temporary: Path, *,
    maintainer: str, create: set[str], allow_downgrade: set[str],
) -> list[Package]:
    project = github_repo(root).split("/")[1]
    names = aur_package_names(project, version)
    query = urlencode([("arg[]", name) for name in names])
    response = json.loads(network(
        ["curl", "-fsS", "--connect-timeout", "10", "--max-time", "30", f"{AUR}/rpc/v5/info?{query}"], root,
    ))
    if response["type"] != "multiinfo":
        raise RuntimeError("AUR did not return package metadata")
    listings = {item["Name"]: item for item in response["results"]}
    packages = []
    for name in names:
        checkout = temporary / name
        checkout.mkdir()
        git(checkout, "init", "--quiet", "--initial-branch=master")
        url = f"{AUR}/{name}.git"
        remote_head = remote_ref(checkout, url, "refs/heads/master")
        if name in listings and listings[name]["Maintainer"] != maintainer:
            raise RuntimeError(f"{name} is maintained by {listings[name]['Maintainer']!r}, expected {maintainer!r}")
        if name not in listings and (remote_head or name not in create):
            raise RuntimeError(f"Could not confirm AUR ownership for {name}; creating a new listing requires --create-aur-package")
        previous = ""
        if remote_head:
            network(["git", "fetch", "--quiet", "--depth=1", url, "refs/heads/master"], checkout)
            if git(checkout, "rev-parse", "FETCH_HEAD") != remote_head:
                raise RuntimeError(f"AUR {name} moved during preflight; rerun publication")
            # Keep the existing index and history. Never execute the previous remote recipe.
            git(checkout, "reset", "--mixed", remote_head)
            previous = package_version(git(checkout, "show", "HEAD:.SRCINFO"))
        elif name in listings:
            raise RuntimeError(f"Existing AUR listing {name} has no master branch")

        recipe = temporary / f"{name}-recipe"
        shutil.copytree(release_dir / "aur" / name, recipe)
        if name.endswith("-git"):
            if git(root, "rev-parse", "--is-shallow-repository") == "true":
                raise RuntimeError("Calculating a -git package version requires full commit history")
            count = git(root, "rev-list", "--count", head)
            short = git(root, "rev-parse", "--short", head)
            pkgbuild = recipe / "PKGBUILD"
            rendered = re.sub(r"^pkgver=.*$", f"pkgver={version.replace('-', '_')}.r{count}.g{short}",
                              pkgbuild.read_text(), flags=re.MULTILINE)
            pkgbuild.write_text(rendered)
        srcinfo = subprocess.check_output(["makepkg", "--printsrcinfo"], cwd=recipe, text=True)
        (recipe / ".SRCINFO").write_text(srcinfo)
        proposed = package_version(srcinfo)
        if previous and int(subprocess.check_output(["vercmp", proposed, previous], text=True)) < 0:
            if name not in allow_downgrade:
                raise RuntimeError(f"{name} would downgrade {previous} to {proposed}; review the version before publishing")
            print(f"AUR downgrade explicitly approved for {name}: {previous} -> {proposed}", flush=True)
        packages.append(Package(name, recipe, checkout, previous, proposed))
    return packages


def publish_packages(packages: list[Package], download: Path) -> None:
    # Check all the recipes for this app before pushing any of them.
    for package in packages:
        recipe = package.recipe
        paths = ["PKGBUILD", ".SRCINFO", *(path.name for path in recipe.glob("*.install"))]
        if package.name.endswith("-bin"):
            (recipe / download.name).symlink_to(download)
        elif not package.name.endswith("-git"):
            network(["env", f"SRCDEST={recipe}", "updpkgsums"], recipe)
        network(["env", f"SRCDEST={recipe}", "makepkg", "--nodeps", "--verifysource"], recipe)
        srcinfo = subprocess.check_output(["makepkg", "--printsrcinfo"], cwd=recipe, text=True)
        if package_version(srcinfo) != package.version:
            raise RuntimeError(f"Generated package version changed for {package.name}")
        (recipe / ".SRCINFO").write_text(srcinfo)
        for name in paths:
            shutil.copyfile(recipe / name, package.checkout / name)
        git(package.checkout, "add", "--", *paths)
        git(package.checkout, "diff", "--cached", "--check")
        if git(package.checkout, "diff", "--cached", "--name-only"):
            if package.version == package.previous_version:
                raise RuntimeError(f"AUR {package.name} {package.version} already exists with a different recipe")
            git(package.checkout, "commit", "-m", f"Update to {package.version}")

    for package in packages:
        commit = git(package.checkout, "rev-parse", "HEAD")
        url = f"{AUR}/{package.name}.git"
        if remote_ref(package.checkout, url, "refs/heads/master") != commit:
            network(["git", "push", f"ssh://aur@aur.archlinux.org/{package.name}.git", "HEAD:refs/heads/master"],
                    package.checkout)
            if remote_ref(package.checkout, url, "refs/heads/master") != commit:
                raise RuntimeError(f"AUR did not publish {package.name} at {commit}")
        print(f"AUR verified {package.name} {package.version} ({commit[:12]}): {AUR}/packages/{package.name}", flush=True)
