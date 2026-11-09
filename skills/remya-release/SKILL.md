---
name: remya-release
description: Prepare, validate, and publish Linux releases for Remya Bar and Remya Settings. Use for release candidates, dev version bumps, GitHub prereleases, or AUR packaging.
---

# Remya application releases

Read the application's `AGENTS.md`. Work on a separate branch and worktree from up-to-date `main`. Put paired worktrees beside each other as `remya-settings` and `remya-bar`. Settings owns the client dependency and goes first at preparation, installation, and publication.

Run the Python helpers from inside the application checkout. Use the absolute path to this skill's `scripts` directory so project and user-level installations behave identically. Keep the same installed skill revision throughout candidate review and publication.

## 1. Prepare and install the candidate

Run [prepare_release.py](scripts/prepare_release.py) with `--version <version>` and the pinned FVM toolchain on the Arch Linux workstation. Dev releases use `0.1.0-dev.YYYYMMDD.N`. Git tags add `v` and AUR replaces the version's hyphen with an underscore. Use the same version for a paired release.

The helper synchronizes versions and lockfiles, regenerates notices, checks the source, builds the Linux x86_64 release, and stages the archive and AUR metadata in `build/release/<version>/`. It installs from the archive and verifies the installed bundle. Keep debug and release checks sequential within each checkout. Inspect the desktop entry, command, running service, and affected desktop interactions.

Verify `SHA256SUMS`, run `makepkg` against the staged archive, and run [publish_release.py](scripts/publish_release.py) with `--version <version> --dry-run`. `makepkg --nodeps` can check the package contents when dependencies are unavailable. Present the candidate and validation results, then stop for explicit approval of the installed application.

## 2. Update README and obtain release approval

After candidate approval, run [review_and_update_readme.py](scripts/review_and_update_readme.py). It uses `agy` with `gemini-3.8-flash-medium` to update README. Use `--base-ref` to override the comparison base. Check the generated text against the installed candidate and repository documentation.

Run `prepare_release.py --version <version> --refresh-readme` to repackage the verified bundle with the updated README. If code, installer inputs, or the Bar client dependency changed, prepare a new candidate and repeat the first approval. Repeat the archive, package, and dry-run checks. Open each application's README with `marktext README.md` from its release worktree. Present the README diff and release notes for explicit approval to commit and publish.

## 3. Publish the approved candidate

After both approvals, commit the reviewed changes in focused commits, fast-forward `main`, and push it as directed by the application's Git rules. Keep the prepared worktree until publishing is verified. Run `publish_release.py --version <version> --confirm-user-approved` in the prepared worktree. Add `--notes-file <path>` for reviewed release notes. The script requires a clean checkout matching local and remote `main`, verifies the candidate, and publishes the tag and GitHub release.

Confirm the remote tag and downloadable assets. Once their checksums match, submit the staged `PKGBUILD` and `.SRCINFO` to the corresponding AUR `remya-settings-bin` or `remya-bar-bin` repository on `master`. Verify package ownership before writing and verify the public AUR version after pushing.

## CI validation

Run `prepare_release.py --ci` in a clean checkout to validate the committed version, build, and test packaging. It writes artifacts to `build/ci-release/`. Use sibling checkouts for both projects, since Bar compiles the Settings client and Settings tests both installers. Workstation installation, Hyprland validation, README review, and publishing are separate from the CI build.
