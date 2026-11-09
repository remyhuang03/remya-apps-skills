#!/usr/bin/env python3
"""Ask Gemini to update README.md after the installed candidate is approved."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import tempfile


sys.dont_write_bytecode = True
from release_checks import app_id, github_repo, repo_root


def resolve_base_ref(root: Path, explicit_base: str | None) -> str:
    if explicit_base:
        return explicit_base
    latest_tag = subprocess.run(
        ["git", "describe", "--tags", "--match", "v*", "--abbrev=0"],
        cwd=root, text=True, capture_output=True,
    )
    if latest_tag.returncode == 0:
        return latest_tag.stdout.strip()
    return subprocess.check_output(
        ["git", "rev-list", "--max-parents=0", "HEAD"], cwd=root, text=True,
    ).splitlines()[0]


def summarize_catalog(root: Path) -> str:
    catalog_path = root / "assets/settings/catalog.json"
    if not catalog_path.is_file():
        return "(catalog.json not found)"
    data = json.loads(catalog_path.read_text())
    categories = len(data.get("categories", []))
    pages = len(data.get("pages", []))
    counts: Counter[str] = Counter()
    total_controls = 0
    for page in data.get("pages", []):
        for group in page["sections"]:
            for control in group.get("controls", []):
                total_controls += 1
                availability = control["availability"]
                counts[availability] += 1
    return (
        f"categories={categories}, pages={pages}, total_controls={total_controls}, "
        f"available={counts['available']}, planned={counts['planned']}, unsupported={counts['unsupported']}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", help="Git ref to review from (default: last reachable v* tag or initial commit)")
    parser.add_argument("--model", default="gemini-3.8-flash-medium", help="Gemini model ID for agy")
    args = parser.parse_args()
    root = repo_root()
    base = resolve_base_ref(root, args.base_ref)
    log = subprocess.check_output(
        ["git", "log", "--first-parent", "--oneline", f"{base}..HEAD"],
        cwd=root, text=True,
    )
    status = subprocess.check_output(
        ["git", "diff", "--stat", base, "--"], cwd=root, text=True,
    )
    readme = root / "README.md"
    original = readme.read_bytes()
    version = next(line.removeprefix("version: ").strip() for line in
                   (root / "pubspec.yaml").read_text().splitlines() if line.startswith("version:"))
    archive_stem = f"{app_id(root)}-{version}-linux-x86_64"
    if app_id(root) == "remya_settings":
        catalog = "Report the exact catalog counts: " + summarize_catalog(root)
        api = (root / "docs/public-api.md").read_text()
        start = api.index("## Remy'a Bar pill layout")
        end = api.index("\n## ", api.index("## Remya Bar keyboard preview"))
        documentation = api[:6000] + api[start:end]
    else:
        catalog = ""
        documentation = (root / "docs/backend.md").read_text()
    prompt = f"""The user approved the installed {app_id(root)} release candidate.
Return the entire revised README as raw Markdown. Begin directly with the
existing opening HTML tag and end after the License section. Do not wrap the
response in a Markdown fence or include an explanation. Do not call any tools.
Use the input README and release context below as evidence.
Keep its headings, badges, and preview notice. Document the current default Bar
layout and distinguish default metrics from optional metrics where relevant.
Explain how to install the Linux x86_64 release archive from
https://github.com/{github_repo(root)}/releases using its tool/install.py.
The exact archive is {archive_stem}.tar.zst and it extracts to {archive_stem}.
Use tar --zstd -xf for the archive and keep SHA256SUMS verification in the
installation instructions. Keep the source build
instructions and state that paired installations install Settings before Bar.
Do not claim a release has already been published. Correct stale feature claims
where the supplied documents resolve them. Keep the prose concise, minimize
changes outside release-relevant sections, and retain documented limitations.
Group related features and use concise descriptions.
{catalog}
Package version: {version}
Changes since {base}:
{log}
Diff stat:
{status}
Repository documentation:
{documentation}
BEGIN CURRENT README
{original.decode("utf-8")}
END CURRENT README
"""
    with tempfile.TemporaryDirectory(prefix="remya-readme-") as temporary:
        result = subprocess.run(
            ["agy", "--model", args.model, "--sandbox", "--disable-slash-commands",
             "--output-format", "json", "--print-timeout", "160s", "--print", prompt],
            cwd=temporary, capture_output=True, text=True, timeout=180,
        )
    if result.returncode:
        raise RuntimeError(f"agy exited with code {result.returncode}: {result.stderr.strip()}")
    response = json.loads(result.stdout)
    if response.get("status") != "SUCCESS":
        raise RuntimeError(f"Gemini README update failed: {response.get('status')}")
    revised = (response["response"].strip() + "\n").encode("utf-8")
    if not revised.startswith(original.splitlines()[0]) or b"## License" not in revised:
        raise RuntimeError("Gemini did not return a complete README")
    if readme.read_bytes() != original:
        raise RuntimeError("README.md was edited while Gemini was running")
    if revised != original:
        readme.write_bytes(revised)
    print(f"Gemini ({args.model}) reviewed {readme.name}")
    subprocess.run(["git", "diff", "--check", "--", "README.md"], cwd=root, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
