#!/usr/bin/env python3
"""Ask Gemini to update README.md and localized docs/README.*.md files."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import subprocess
import sys


DEFAULT_MODEL = "gemini-4-hydrogen-medium"

LOCALIZED_READMES: tuple[tuple[str, str], ...] = (
    ("zh-CN", "简体中文"),
    ("zh-TW", "繁體中文"),
    ("ru", "Русский"),
    ("de", "Deutsch"),
    ("es", "Español"),
    ("ja", "日本語"),
)


def repo_root() -> Path:
    root = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip())
    if not (root / "pubspec.yaml").is_file():
        raise RuntimeError("Run from a Remya application checkout containing pubspec.yaml")
    return root


def read_pubspec_meta(root: Path) -> tuple[str, str]:
    text = (root / "pubspec.yaml").read_text(encoding="utf-8")
    name_match = re.search(r"^name:[ \t]*(\S+)[ \t]*$", text, re.MULTILINE)
    ver_match = re.search(r"^version:[ \t]*(\S+)[ \t]*$", text, re.MULTILINE)
    if not name_match or not ver_match:
        raise RuntimeError("Unable to parse name and version from pubspec.yaml")
    return name_match.group(1), ver_match.group(1)


def aur_package_names(project: str, channel: str) -> tuple[str, ...]:
    if channel == "dev":
        return (f"{project}-git",)
    return (project, f"{project}-bin")


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


def gather_documentation(root: Path, app: str) -> tuple[str, str]:
    if app == "remya_settings":
        catalog = "Report the exact catalog counts: " + summarize_catalog(root)
        api = (root / "docs/public-api.md").read_text(encoding="utf-8")
        start = api.index("## Remy'a Bar pill layout")
        end = api.index("\n## ", api.index("## Remya Bar keyboard preview"))
        return catalog, api[:6000] + api[start:end]
    backend_doc = root / "docs/backend.md"
    if backend_doc.is_file():
        return "", backend_doc.read_text(encoding="utf-8")
    return "", "(no additional backend documentation file)"


def run_agy(model: str, prompt: str) -> bytes:
    import tempfile

    with tempfile.TemporaryDirectory(prefix="remya-readme-") as temporary:
        result = subprocess.run(
            ["agy", "--model", model, "--sandbox", "--disable-slash-commands",
             "--output-format", "json", "--print-timeout", "160s", "--print", prompt],
            cwd=temporary, capture_output=True, text=True, timeout=180,
        )
    if result.returncode:
        raise RuntimeError(f"agy exited with code {result.returncode}: {result.stderr.strip()}")
    response = json.loads(result.stdout)
    if response.get("status") != "SUCCESS":
        raise RuntimeError(f"Gemini README update failed: {response.get('status')}")
    return (response["response"].strip() + "\n").encode("utf-8")


def language_switcher_html(*, active_code: str | None) -> str:
    items = []
    if active_code is None:
        items.append("  <strong>English</strong>")
    else:
        items.append('  <a href="../README.md">English</a>')
    for code, label in LOCALIZED_READMES:
        if code == active_code:
            items.append(f"  <strong>{label}</strong>")
        else:
            href = f"docs/README.{code}.md" if active_code is None else f"README.{code}.md"
            items.append(f'  <a href="{href}">{label}</a>')
    return '<p align="center">\n' + " ·\n".join(items) + "\n</p>"


def build_english_prompt(
    *,
    app: str,
    repo: str,
    version: str,
    channel: str,
    aur_pkgs: tuple[str, ...],
    archive_stem: str,
    catalog: str,
    base: str,
    log: str,
    status: str,
    documentation: str,
    original_text: str,
) -> str:
    switcher = language_switcher_html(active_code=None)
    aur_list = ", ".join(aur_pkgs)
    return f"""The user approved the installed {app} release candidate.
Return the entire revised English README.md as raw Markdown. Begin directly with
the centered header HTML (<p align="center">) and end after the License section.
Do not wrap the response in a Markdown fence or include an explanation. Do not call any tools.

Follow this exact README structure and visual style:
1. Centered app icon: `<p align="center"><img src="..." width="128" alt="..."></p>` (keep the existing icon path).
2. Centered `<h1 align="center">...</h1>` and `<h3 align="center">...</h3>` concise tagline.
3. Centered shields.io badges (`<p align="center">...`) for Platform (`Arch Linux | Hyprland`), Flutter, Rust, and License.
4. Centered 7-language switcher immediately below the badges, matching this exact HTML:
{switcher}
5. Short intro paragraph and 3 concise highlight bullets, followed by a GitHub-style `> [!WARNING]` notice reflecting the `{channel}` stage (for `stable`, omit the warning or use `> [!NOTE]` only if needed).
6. Emoji-prefixed top-level sections:
   - `## 🚀 Installation`
   - `## ✨ Features`
   - `## ⌨️ Development`
   - `## 📜 License`

Content requirements:
- Use the input README and release context below as evidence.
- Document the current default Bar layout and distinguish default metrics from optional metrics where relevant.
- In `## 🚀 Installation`:
  - Show AUR installation for the `{channel}` channel using `{aur_list}` (`paru -S ... || yay -S ...`).
  - Explain how to install the Linux x86_64 release archive from https://github.com/{repo}/releases using `tool/install.py`.
  - The exact archive is `{archive_stem}.tar.zst` and it extracts to `{archive_stem}`.
  - Use `tar --zstd -xf` for the archive and keep `SHA256SUMS` verification in the installation instructions.
  - State that paired installations install Settings before Bar.
- In `## ✨ Features`: group related features concisely and preserve existing screenshot/demo image paths if present.
- In `## ⌨️ Development`: include `### Tech Stack & Architecture`, `### Prerequisites & Setup`, and `### Validation & Local Installation`, keeping source build instructions.
- In `## 📜 License`: link to `[PolyForm Noncommercial License 1.0.0](LICENSE)`.
- Do not claim a release has already been published. Correct stale feature claims where the supplied documents resolve them, and retain documented limitations.
{catalog}
Package version: {version} (channel: {channel})
Changes since {base}:
{log}
Diff stat:
{status}
Repository documentation:
{documentation}
BEGIN CURRENT README
{original_text}
END CURRENT README
"""


def build_translation_prompt(
    *,
    lang_code: str,
    lang_label: str,
    english_readme: str,
    existing_translation: str | None,
) -> str:
    switcher = language_switcher_html(active_code=lang_code)
    existing_block = (
        f"BEGIN EXISTING {lang_code} README\n{existing_translation}\nEND EXISTING {lang_code} README\n"
        if existing_translation is not None
        else "(No existing translation file yet; create a complete translation.)\n"
    )
    return f"""Translate and synchronize the English README.md into {lang_label} ({lang_code}) for `docs/README.{lang_code}.md`.
Return the entire localized Markdown file as raw Markdown. Begin directly with `<p align="center">` and end after the License section.
Do not wrap the response in a Markdown fence or include an explanation. Do not call any tools.

Requirements:
- Preserve the exact layout, HTML tags, shields.io badges, code blocks, command lines, file paths, and emoji section headings (`## 🚀 ...`, `## ✨ ...`, `## ⌨️ ...`, `## 📜 ...`).
- Use this exact language switcher HTML:
{switcher}
- Adjust relative repository links for `docs/README.{lang_code}.md`:
  - `assets/icons/...` -> `../assets/icons/...`
  - `docs/images/...` -> `images/...`
  - `(LICENSE)` -> `(../LICENSE)`
- Translate all prose, headings, alert notices, and comments naturally and accurately into {lang_label}. Do not translate package names, CLI flags, symbol names, or code identifiers.

BEGIN ENGLISH README
{english_readme}
END ENGLISH README
{existing_block}"""


def validate_readme_bytes(content: bytes, *, is_localized: bool) -> None:
    if not content.lstrip().startswith(b'<p align="center">'):
        raise RuntimeError('Gemini output must begin with <p align="center">')
    if b'<h1 align="center">' not in content:
        raise RuntimeError('Gemini output is missing <h1 align="center">')
    expected_license = b"(../LICENSE)" if is_localized else b"(LICENSE)"
    if expected_license not in content:
        raise RuntimeError(f"Gemini output is missing license link {expected_license.decode()}")


def update_file_with_gemini(target: Path, model: str, prompt: str, *, is_localized: bool) -> bool:
    original = target.read_bytes() if target.is_file() else None
    revised = run_agy(model, prompt)
    validate_readme_bytes(revised, is_localized=is_localized)
    current = target.read_bytes() if target.is_file() else None
    if current != original:
        raise RuntimeError(f"{target} was edited while Gemini was running")
    if revised == original:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(revised)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", help="Git ref to review from (default: last reachable v* tag or initial commit)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Gemini model ID for agy (default: {DEFAULT_MODEL})")
    parser.add_argument(
        "--channel",
        choices=("dev", "alpha", "beta", "stable"),
        help="Release channel for AUR package names and warning banner (default: dev for -dev., else beta)",
    )
    parser.add_argument("--skip-translations", action="store_true", help="Only update root README.md")
    parser.add_argument("--langs", help="Comma-separated localized language codes (default: all supported)")
    args = parser.parse_args()

    root = repo_root()
    app, version = read_pubspec_meta(root)
    project = app.replace("_", "-")
    channel = args.channel or ("dev" if "-dev." in version else "beta")
    aur_pkgs = aur_package_names(project, channel)
    base = resolve_base_ref(root, args.base_ref)
    log = subprocess.check_output(
        ["git", "log", "--first-parent", "--oneline", f"{base}..HEAD"],
        cwd=root, text=True,
    )
    status = subprocess.check_output(
        ["git", "diff", "--stat", base, "--"], cwd=root, text=True,
    )
    readme = root / "README.md"
    original_text = readme.read_text(encoding="utf-8")
    archive_stem = f"{app}-{version}-linux-x86_64"
    catalog, documentation = gather_documentation(root, app)

    en_prompt = build_english_prompt(
        app=app,
        repo=f"remyhuang03/{project}",
        version=version,
        channel=channel,
        aur_pkgs=aur_pkgs,
        archive_stem=archive_stem,
        catalog=catalog,
        base=base,
        log=log,
        status=status,
        documentation=documentation,
        original_text=original_text,
    )
    update_file_with_gemini(readme, args.model, en_prompt, is_localized=False)
    print(f"Gemini ({args.model}) reviewed {readme.name}")

    if not args.skip_translations:
        english_readme = readme.read_text(encoding="utf-8")
        selected = set(args.langs.split(",")) if args.langs else {code for code, _ in LOCALIZED_READMES}
        unknown = selected - {code for code, _ in LOCALIZED_READMES}
        if unknown:
            raise ValueError(f"Unsupported language code(s): {', '.join(sorted(unknown))}")
        for code, label in LOCALIZED_READMES:
            if code not in selected:
                continue
            loc_path = root / f"docs/README.{code}.md"
            existing = loc_path.read_text(encoding="utf-8") if loc_path.is_file() else None
            tr_prompt = build_translation_prompt(
                lang_code=code,
                lang_label=label,
                english_readme=english_readme,
                existing_translation=existing,
            )
            rel = f"docs/README.{code}.md"
            update_file_with_gemini(loc_path, args.model, tr_prompt, is_localized=True)
            print(f"Gemini ({args.model}) synced {rel}")

    check_targets = ["README.md", *(str(p.relative_to(root)) for p in sorted(root.glob("docs/README.*.md")))]
    subprocess.run(["git", "diff", "--check", "--", *check_targets], cwd=root, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
