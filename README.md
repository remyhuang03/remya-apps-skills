# Remya Apps Skills

Shared agent skills for the Remya Linux desktop applications.

## Install

Install the skills once at the user level (works with Codex, Claude Code, Antigravity / `agy`, and other agents supported by `npx skills`):

```bash
npx skills add remyhuang03/remya-apps-skills --skill remya-release --skill remya-readme --global
```

Use `$remya-release` from either application checkout. Keep the same installed revision while preparing, reviewing, and publishing a candidate.

## Skills

- [remya-release](skills/remya-release/SKILL.md) prepares and verifies releases for [Remya Bar](https://github.com/remyhuang03/remya-bar) and [Remya Settings](https://github.com/remyhuang03/remya-settings). It preserves the workstation and README approval steps for GitHub and AUR releases.
- [remya-readme](skills/remya-readme/SKILL.md) updates and synchronizes multilingual README files (`README.md` and `docs/README.*.md` in 7 languages) using `gemini-4-hydrogen-medium`.

## CI

The skill validation workflow tests installation and script discovery. Use **Validate Remya apps** in Actions to run the Linux build and package checks against pinned Bar and Settings commits. The CI packages are validation artifacts. Publish the workstation-approved bundle using the release skill.

The source uses the [PolyForm Noncommercial License 1.0.0](LICENSE).
