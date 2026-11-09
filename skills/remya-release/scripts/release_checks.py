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


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def repo_root() -> Path:
    root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel"))
    app_id(root)
    return root


def app_id(root: Path) -> str:
    match = re.search(r"^name:[ \t]*(remya_bar|remya_settings)[ \t]*$", (root / "pubspec.yaml").read_text(), re.MULTILINE)
    if not match:
        raise RuntimeError("Run from a Remya Bar or Remya Settings checkout")
    return match.group(1)


def skill_root() -> Path:
    return Path(__file__).resolve().parents[1]


def skill_sha256() -> str:
    root = skill_root()
    files = [root / "SKILL.md", *root.glob("scripts/*.py"), *root.glob("resources/*.in")]
    return files_sha256(root, [path.relative_to(root) for path in files])


def github_repo(root: Path) -> str:
    return "remyhuang03/" + app_id(root).replace("_", "-")


def sibling_client_sha256(root: Path) -> str:
    sibling = root.parent / "remya-settings"
    client = "packages/remya_settings_client"
    if not (sibling / client / "pubspec.yaml").is_file():
        raise RuntimeError("Check out the matching Remya Settings repository beside Bar")
    return source_sha256(sibling, client)


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
