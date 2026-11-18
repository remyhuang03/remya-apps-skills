---
name: remya-release
description: Prepare, validate, and publish Linux releases for Remya Core, Settings, Bar, Launcher, and Screen Capture. Use for release candidates, dev version bumps, GitHub releases, or AUR packaging.
---

# Remya application releases

Read the application's `AGENTS.md`. Work on a separate branch and worktree from up-to-date `main`. Prepare, install, and publish in this order: **core, settings, bar, launcher, screen-capture**. Each application is independently installable and depends only on remya-core among Remya packages; never require another GUI application.

Keep the matching Core checkout next to each dependent application as `remya-core`. Core owns `packages/remya_client`, `packages/remya_ui`, and the camera preview crate used by Settings. Source packages pin the sibling Core tag recorded in the candidate manifest (`core_version`); it must already be tagged or be tagged before publishing the AUR package. The helper hashes both Core package trees before and after building, and checks them again for README refresh and publication. Screen Capture currently supports numbered releases (alpha, beta, and stable).

Core builds `remyad` and `remya-core-update` with Cargo and compiles `tool/remya_cli.dart` to `remya` with `fvm dart compile exe`. Its archive carries only these three binaries under `target/release/`, plus the activation template, the updater polkit rule, license and documentation inputs. System packages install binaries under `/usr/lib/remya-core`, expose `/usr/bin/remya`, and install `top.remya.Core1.service` under `/usr/share/dbus-1/services` and `top.remya.core.service` under `/usr/lib/systemd/user`. They also install `50-top.remya.core.update.rules` under `/usr/share/polkit-1/rules.d`, which lets `wheel` members run only the packaged `remya-core-update upgrade [--include-held]`, so packaged installs need no separate updater setup. `remya-core.install` removes the old source-installed `remya-settings-update` and its rule (only when the rule carries that installer's marker).

Settings packages contain only the GUI, including its AppImage desktop handler; no daemon, service unit, D-Bus activation, CLI or updater. Bar, Launcher and Screen Capture install their `linux/packaging/<id>.json` registrations under `/usr/share/remya/apps/` and retain their desktop Settings actions. Bar's user unit wants and follows `top.remya.core.service`. Launcher also packages `remya-launcher-clipboard` and `top.remya.launcher.clipboard.service`, preserving its clipboard privacy settings.

Run the single-app helpers from inside the application checkout. Use the absolute path to this skill's `scripts` directory. Keep the same skill revision throughout candidate review and publication.

## Batch releases

Use [batch_release.py](scripts/batch_release.py) for multiple apps. Save a JSON plan outside the app worktrees. Paths are relative to the plan, or absolute. `notes` is optional. Set `"skip_readme": true` only for apps whose README phase the user waived.

```json
[
  {"checkout": "remya-core", "version": "0.2.0", "notes": "notes/core.md"},
  {"checkout": "remya-settings", "version": "0.1.0-dev.20261118.0", "notes": "notes/settings.md"}
]
```

Run `python <scripts>/batch_release.py <phase> <plan.json>` once for each phase below. The batch runs Core first, then Settings, Bar, Launcher and Screen Capture, and stops at the first failure with a list of completed apps. Use `--only remya-<app>` to retry individual candidates. `check` is an optional, read-only publication dry run. Commit and push approved app changes according to each app's Git rules before running `publish`.

## Choose the version before preparing

When the user specifies a channel without a version, determine the bump from the actual release diff. Verify the published tags and AUR package versions. Compare the candidate with the latest published release in the target channel, or with the most recent distributed dev build when preparing the first numbered release. For an application with no previously distributed version, use its initial version. Inspect `git diff <published-tag> HEAD`, including any additional changes intended for release. Use commit titles to locate changes and read the diff to establish their effect on features, public interfaces, saved settings and installation.

- Increment `Y` and reset `Z` for new user-facing capabilities or substantial additions to existing ones. In the `0.x` series, do the same for incompatible changes to public interfaces, saved settings or installation contracts. From `1.0.0` onward, increment `X` for incompatible public changes.
- Increment `Z` for fixes, internal refactors, packaging maintenance or documentation that preserve the existing feature set and contracts.
- For dev releases following a dev build, keep the numbered base for fixes and maintenance and advance it for features or incompatibilities. If the previous release is numbered, choose the next numbered base from the diff. Append the next unused `-dev.YYYYMMDD.N` suffix for that base and date.

Use `vercmp` to verify that each existing AUR package will upgrade to the proposed version. Present the baseline, proposed version and specific code changes that justify the bump before preparing the candidate.

## 1. Prepare and install the candidate

Run [prepare_release.py](scripts/prepare_release.py) with `--version <version>` and the pinned FVM toolchain on the Arch Linux workstation. Dev releases use `X.Y.Z-dev.YYYYMMDD.N`. For alpha, beta and stable, use `X.Y.Z` and pass `--allow-stable`. Git tags add `v` and AUR replaces the version's hyphen with an underscore. Dependent applications record the matching Core version independently of their own release version.

Use batch phase `prepare` or the single-app helper. It synchronizes versions, lockfiles and notices, runs source validation, builds the Linux x86_64 archive, verifies its checksum, and runs `makepkg --nodeps` against that archive. It records the resulting package file list in `build/release/<version>/package-files.txt` and stages AUR metadata under `aur/<pkgname>/`. The helper installs from the archive and checks the installed bundle, command, desktop entry and any applicable running service. Inspect affected desktop interactions before presenting the candidate.

The helper checks Flutter's bundled `data/flutter_assets/version.json` against `pubspec.yaml` and checks the version reported by the running Core service. Open the installed Settings, Bar and Screen Capture About pages and confirm that each shows its full release version, including any dev suffix.

Present the candidate and validation results, then stop for explicit approval of the installed application. If the user waived the README workflow, present the release notes and request publication approval together with candidate approval.

## 2. Update README and obtain release approval

After candidate approval, use the minimal correction workflow in `remya-readme` unless the user waived this phase. If the installed README skill predates that workflow, edit the confirmed passages directly. Verify corrections against the installed candidate and repository documentation, and keep the existing presentation.

Use batch phase `refresh-readme` or `prepare_release.py --version <version> --refresh-readme` after reviewing the READMEs. The helper checks candidate integrity, reuses the archive if the text is unchanged, and packages and verifies a new archive if the README changed. If code, installer inputs, or either shared Core package changed, prepare a new candidate and repeat the first approval. Open each application's README with `marktext README.md` from its release worktree. Present the README diffs and release notes for explicit approval to commit and publish.

## 3. Publish the approved candidate

After candidate and publication approval, commit the reviewed changes in focused commits, fast-forward `main`, and push it as directed by the application's Git rules. Keep the prepared worktree until publishing is verified. Run batch phase `publish --confirm-user-approved --aur-maintainer remya`, or run [publish_release.py](scripts/publish_release.py) with `--version <version>` and the same flags. For a single app, add `--notes-file <path>` for reviewed notes and `--skip-readme` when the user waived README review.

The script checks the clean checkout against local and remote `main`, verifies the candidate, pushes the tag, and publishes the GitHub release. Every `X.Y.Z` version is a regular GitHub release. Only dev versions are GitHub prereleases. Put alpha or beta labels in the release notes. The script verifies the remote tag and checksums of the downloaded assets before pushing anything to AUR.

With `--aur-maintainer`, the script checks AUR ownership, the published Core tag for dependents and the AUR version against the current Git recipe. Numbered builds publish source and `-bin` packages. Dev builds publish `-git` with an initial version derived from the approved commit. The helper fills in source archive checksums, generates `.SRCINFO`, runs `makepkg --verifysource`, pushes AUR `master` and verifies the remote commit. Use `--create-aur-package <package>` only for an approved new listing. A downgrade requires explicit approval and `--allow-aur-downgrade <package>`. The script uses the epoch in the staged recipe, if any.

If publication interrupts, rerun `publish` with the same plan and approvals. Matching remote tags, GitHub releases and AUR packages are reused. Conflicting remote content stops the run. When replacing a package published under the wrong suffix, publish and verify the destination first. If the user authorized retiring the old listing, file an AUR merge request into the destination and verify its status.

## CI validation

After pushing the approved commits, use batch phase `ci` to wait for each app's Linux workflow on the exact commit in its release worktree. The phase reports the run URL and fails on an unsuccessful run.

Run `prepare_release.py --ci` in a clean checkout to validate the committed version, build, and test packaging. It writes artifacts to `build/ci-release/`. Use a matching sibling `remya-core` checkout for every dependent app, including Settings camera preview builds. Build Core first; CI does not install it on the workstation. Workstation installation, Hyprland validation, README review, and publishing are separate from the CI build.
