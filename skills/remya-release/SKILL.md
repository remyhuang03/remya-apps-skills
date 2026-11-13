---
name: remya-release
description: Prepare, validate, and publish Linux releases for Remya Core, Settings, Bar, Launcher, and Screen Capture. Use for release candidates, dev version bumps, GitHub releases, or AUR packaging.
---

# Remya application releases

Read the application's `AGENTS.md`. Work on a separate branch and worktree from up-to-date `main`. Prepare, install, and publish in this order: **core, settings, bar, launcher, screen-capture**. Each application is independently installable and depends only on remya-core among Remya packages; never require another GUI application.

Keep the matching Core checkout next to each dependent application as `remya-core`. Core owns `packages/remya_client`, `packages/remya_ui`, and the camera preview crate used by Settings. Source packages pin the sibling Core tag recorded in the candidate manifest (`core_version`); it must already be tagged or be tagged before publishing the AUR package. The helper hashes both Core package trees before and after building, and checks them again for README refresh and publication. Screen Capture currently supports numbered releases (alpha, beta, and stable).

Core builds `remyad` and `remya-core-update` with Cargo and compiles `tool/remya_cli.dart` to `remya` with `fvm dart compile exe`. Its archive carries only these three binaries under `target/release/`, plus the installer, activation template, license and documentation inputs. System packages install binaries under `/usr/lib/remya-core`, expose `/usr/bin/remya`, and install `top.remya.Core1.service` under `/usr/share/dbus-1/services` and `top.remya.core.service` under `/usr/lib/systemd/user`. The optional root updater setup remains a separate, explicitly authorized `tool/install_system_updater.py` operation.

Settings packages contain only the GUI, including its AppImage desktop handler; no daemon, service unit, D-Bus activation, CLI or updater. Bar, Launcher and Screen Capture install their `linux/packaging/<id>.json` registrations under `/usr/share/remya/apps/` and retain their desktop Settings actions. Bar's user unit wants and follows `top.remya.core.service`. Launcher also packages `remya-launcher-clipboard` and `top.remya.launcher.clipboard.service`, preserving its clipboard privacy settings.

Run the Python helpers from inside the application checkout. Use the absolute path to this skill's `scripts` directory so project and user-level installations behave identically. Keep the same installed skill revision throughout candidate review and publication.

## 1. Prepare and install the candidate

Run [prepare_release.py](scripts/prepare_release.py) with `--version <version>` and the pinned FVM toolchain on the Arch Linux workstation. Dev releases use `0.1.0-dev.YYYYMMDD.N`; all non-dev releases (alpha, beta, and stable) use `X.Y.Z` without any suffix and pass `--allow-stable`. Git tags add `v` and AUR replaces the version's hyphen with an underscore. Dependent applications record the matching Core version independently of their own release version.

The helper synchronizes versions and lockfiles, regenerates notices, checks the source, builds the Linux x86_64 release, and stages the archive and AUR metadata in `build/release/<version>/` (with target AUR packages under `build/release/<version>/aur/<pkgname>/`). It installs from the archive and verifies the installed bundle. Keep debug and release checks sequential within each checkout. Inspect the desktop entry, command, running service, and affected desktop interactions.

Verify `SHA256SUMS`, run `makepkg` against the staged archive, and run [publish_release.py](scripts/publish_release.py) with `--version <version> --dry-run`. `makepkg --nodeps` can check the package contents when dependencies are unavailable. Present the candidate and validation results, then stop for explicit approval of the installed application. If the user waived the README workflow, present the release notes and request publication approval together with candidate approval.

## 2. Update README and obtain release approval

Unless the user waived this phase, after candidate approval use the `remya-readme` skill (`skills/remya-readme/scripts/update_readme.py --channel <dev|alpha|beta|stable>`) to update `README.md` and synchronize `docs/README.{zh-CN,zh-TW,ru,de,es,ja}.md` with `gemini-4-hydrogen-medium` in the `remya-screen-capture` style. Check the generated text against the installed candidate and repository documentation.

Run `prepare_release.py --version <version> --refresh-readme` to repackage the verified bundle with the updated READMEs. If code, installer inputs, or either shared Core package changed, prepare a new candidate and repeat the first approval. Repeat the archive, package, and dry-run checks. Open each application's README with `marktext README.md` from its release worktree. Present the README diffs and release notes for explicit approval to commit and publish.

## 3. Publish the approved candidate

After candidate and publication approval, commit the reviewed changes in focused commits, fast-forward `main`, and push it as directed by the application's Git rules. Keep the prepared worktree until publishing is verified. Run `publish_release.py --version <version> --confirm-user-approved` in the prepared worktree (add `--skip-readme` if the user waived the README workflow, and `--notes-file <path>` for reviewed release notes). The script requires a clean checkout matching local and remote `main`, verifies the candidate, and publishes the tag and GitHub release. Only dev versions are marked Pre-release on GitHub. Publish every `X.Y.Z` version as a regular GitHub Release, including alpha and beta builds. The alpha or beta label belongs in the release notes.

Confirm the remote tag, GitHub release status, and downloadable assets. Once their checksums match, submit the staged `PKGBUILD` and `.SRCINFO` from `build/release/<version>/aur/<pkgname>/` to AUR `master` according to the release channel:
- **Alpha / Beta / Stable release (`X.Y.Z`)**: publish to both `remya-<app_name>` (download all tagged source archives, fill in their `sha256sums`, regenerate `.SRCINFO`, and run `makepkg --verifysource`) and `remya-<app_name>-bin`.
- **Dev release (`X.Y.Z-dev.YYYYMMDD.N`)**: publish to `remya-<app_name>-git`.

Verify package ownership before writing and verify the public AUR version after pushing. For a `-git` package, update the recipe's initial `pkgver` using the published checkout and regenerate `.SRCINFO`. When replacing a package published under the wrong suffix, publish and verify the destination first. If the user authorized retiring the old listing, file an AUR merge request into the destination and verify its status.

## CI validation

Run `prepare_release.py --ci` in a clean checkout to validate the committed version, build, and test packaging. It writes artifacts to `build/ci-release/`. Use a matching sibling `remya-core` checkout for every dependent app, including Settings camera preview builds. Build Core first; CI does not install it on the workstation. Workstation installation, Hyprland validation, README review, and publishing are separate from the CI build.
