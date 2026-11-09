# Remya Apps Skills

Shared agent skills for the Remya Linux desktop applications.

## Install

From a Remya application checkout, install the release skill for Codex:

```bash
npx skills add remyhuang03/remya-apps-skills --skill remya-release --agent codex
```

Add `--global` for a user-level install when preparing a release from a clean checkout. List skills or pin a repository version:

```bash
npx skills add remyhuang03/remya-apps-skills --list
npx skills add remyhuang03/remya-apps-skills@v0.1.1 --skill remya-release --agent codex
```

## Skills

- [remya-release](skills/remya-release/SKILL.md) prepares and verifies releases for [Remya Bar](https://github.com/remyhuang03/remya-bar) and [Remya Settings](https://github.com/remyhuang03/remya-settings). It preserves the workstation and README approval steps for GitHub and AUR releases.

## CI

The skill validation workflow tests installation and script discovery. Use **Validate Remya apps** in Actions to run the Linux build and package checks against pinned Bar and Settings commits. The CI packages are validation artifacts. Publish the workstation-approved bundle using the release skill.

The source uses the [PolyForm Noncommercial License 1.0.0](LICENSE).
