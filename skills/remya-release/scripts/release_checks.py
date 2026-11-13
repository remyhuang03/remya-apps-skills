"""Read-only checks shared by the preparation and publication commands."""
import hashlib
import os
from pathlib import Path
import re
import subprocess

VERSION = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-dev\.[0-9]{8}\.(?:0|[1-9][0-9]*))?"
)


def is_dev_version(version: str) -> bool:
    return "-dev." in version


def aur_package_names(project: str, version: str) -> tuple[str, ...]:
    if is_dev_version(version):
        return (f"{project}-git",)
    return (project, f"{project}-bin")


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def repo_root() -> Path:
    root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel"))
    app_id(root)
    return root


def app_id(root: Path) -> str:
    if not (root / "pubspec.yaml").exists() and (root / "tool/remya_cli.dart").is_file():
        return "remya_core"
    match = re.search(r"^name:[ \t]*(remya_bar|remya_settings|remya_launcher|remya_screen_capture)[ \t]*$", (root / "pubspec.yaml").read_text(), re.MULTILINE)
    if not match:
        raise RuntimeError("Run from a Remya application checkout")
    return match.group(1)


def skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


def skill_sha256() -> str:
    root = skill_root()
    files = [root / "SKILL.md", *root.glob("scripts/*.py"), *root.glob("resources/*.in")]
    return files_sha256(root, [path.relative_to(root) for path in files])


def github_repo(root: Path) -> str:
    return "remyhuang03/" + app_id(root).replace("_", "-")


def sibling_core_sha256(root: Path) -> str:
    sibling = root.parent / "remya-core"
    packages = ("packages/remya_client", "packages/remya_ui")
    if not all((sibling / package / "pubspec.yaml").is_file() for package in packages):
        raise RuntimeError("Check out the matching Remya Core repository beside the application")
    return source_sha256(sibling, *packages)


def release_bundle(root: Path) -> Path:
    return root / ("target/release" if app_id(root) == "remya_core" else "build/linux/x64/release/bundle")


def sha256_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def files_sha256(root: Path, paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(set(paths)):
        absolute = root / path
        if absolute.is_symlink():
            value = b"link:" + os.fsencode(absolute.readlink())
        elif absolute.is_file():
            value = sha256_file(absolute).encode("ascii")
        else:
            continue
        digest.update(os.fsencode(path) + b"\0" + value + b"\0")
    return digest.hexdigest()


def source_sha256(root: Path, *pathspecs: str) -> str:
    files = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", *pathspecs], cwd=root
    )
    return files_sha256(root, [Path(os.fsdecode(path)) for path in files.split(b"\0") if path])


def bundle_sha256(bundle: Path, app_id: str) -> str:
    if app_id == "remya_core":
        binaries = [Path(name) for name in ("remyad", "remya-core-update", "remya")]
        if not all((bundle / binary).is_file() for binary in binaries):
            raise RuntimeError(f"Incomplete release bundle: {bundle}")
        return files_sha256(bundle, binaries)
    if app_id == "remya_launcher" and not (bundle / "remya-launcher-clipboard").is_file():
        raise RuntimeError(f"Incomplete release bundle: {bundle}")
    if not (bundle / app_id).is_file() or not (bundle / "lib/libapp.so").is_file():
        raise RuntimeError(f"Incomplete release bundle: {bundle}")
    return files_sha256(bundle, [path.relative_to(bundle) for path in bundle.rglob("*")])


def installed_bundle(app_id: str) -> Path:
    data = Path(os.environ.get("XDG_DATA_HOME", ""))
    if not data.is_absolute():
        home = Path(os.environ.get("HOME", ""))
        if not home.is_absolute():
            raise RuntimeError("An absolute HOME or XDG_DATA_HOME is required")
        data = home / ".local/share"
    return data / "remya" / app_id
