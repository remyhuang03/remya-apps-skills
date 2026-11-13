---
name: remya-readme
description: Generate, update, and synchronize multilingual README files (English and 6 translations) for Remya Linux desktop applications using Gemini. Use when updating documentation for a release or syncing docs/README.*.md translations.
---

# Remya multilingual README updates

Run [update_readme.py](scripts/update_readme.py) from inside the application checkout. It uses `agy` with `gemini-4-hydrogen-medium` to update the English `README.md` and synchronize the 6 localized READMEs under `docs/`.

## Supported languages

Every Remya application README supports 7 languages:
- English: `README.md`
- 简体中文: `docs/README.zh-CN.md`
- 繁體中文: `docs/README.zh-TW.md`
- Русский: `docs/README.ru.md`
- Deutsch: `docs/README.de.md`
- Español: `docs/README.es.md`
- 日本語: `docs/README.ja.md`

## Style and structure

Follow the `remya-screen-capture` README style:
- Centered header block: `<p align="center">` app icon (`assets/icons/app_icon-full.svg`, `width="128"`, or `../assets/icons/app_icon-full.svg` in `docs/README.*.md`), `<h1 align="center">` title, `<h3 align="center">` concise tagline, shields.io badges (`Platform`, `Flutter`, `Rust`, `License`), and the 7-language switcher bar.
- Short intro paragraph + 3 concise highlight bullets, followed by a GitHub-style `> [!WARNING]` notice for `dev` / `alpha` / `beta` stages.
- Emoji-prefixed top-level headings:
  - `## 🚀 Installation`: AUR installation (`remya-<app>` and `remya-<app>-bin` for `alpha`/`beta`/`stable`, or `remya-<app>-git` for `dev`), GitHub release archive (`tar --zstd -xf`, `SHA256SUMS`, `tool/install.py`), and note that paired installations install Settings before Bar.
  - `## ✨ Features`: concise, grouped feature descriptions with `docs/images/` demos where available.
  - `## ⌨️ Development`: `### Tech Stack & Architecture`, `### Prerequisites & Setup`, and `### Validation & Local Installation`.
  - `## 📜 License`: link to `LICENSE` (or `../LICENSE` in `docs/README.*.md`).

## Usage

```bash
python3 /path/to/skills/remya-readme/scripts/update_readme.py --channel beta
```

Use `--base-ref` to override the git comparison base, `--langs zh-CN,ja` to update specific translations, or `--skip-translations` to update only `README.md`.
