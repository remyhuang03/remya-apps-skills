#!/usr/bin/env python3
"""Run one release phase for a batch of Remya worktrees in dependency order."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

sys.dont_write_bytecode = True
from release_checks import VERSION, app_id, aur_package_names, git, github_repo, is_dev_version, network

SCRIPTS = Path(__file__).resolve().parent
ORDER = ("remya_core", "remya_settings", "remya_bar", "remya_launcher", "remya_screen_capture")


def load_plan(path: Path) -> dict:
    entries = json.loads(path.read_text())
    if not isinstance(entries, list) or not entries:
        raise ValueError("The plan must be a nonempty JSON array")
    plan = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) - {"checkout", "version", "notes", "skip_readme"}:
            raise ValueError("Each release needs checkout and version, with optional notes and skip_readme")
        if not isinstance(entry.get("checkout"), str) or not isinstance(entry.get("version"), str):
            raise ValueError("checkout and version must be strings")
        root = (path.parent / entry["checkout"]).resolve(strict=True)
        if Path(git(root, "rev-parse", "--show-toplevel")).resolve() != root:
            raise ValueError(f"Expected a checkout root: {root}")
        app = app_id(root)
        version = entry["version"]
        if app in plan or not VERSION.fullmatch(version) or (app == "remya_screen_capture" and is_dev_version(version)):
            raise ValueError(f"Duplicate application or unsupported release version: {app} {version}")
        if not isinstance(entry.get("skip_readme", False), bool):
            raise ValueError("skip_readme must be a boolean")
        if "notes" in entry and not isinstance(entry["notes"], str):
            raise ValueError("notes must be a path")
        plan[app] = (root, entry)
    if "remya_core" in plan:
        core = plan["remya_core"][0]
        if any(app != "remya_core" and (root.parent / "remya-core").resolve() != core
               for app, (root, _) in plan.items()):
            raise ValueError("Every dependent worktree must use the batch's sibling remya-core worktree")
    return plan


def watch_ci(root: Path) -> int:
    commit = git(root, "rev-parse", "HEAD")
    repo = github_repo(root)
    command = ["gh", "run", "list", "--repo", repo, "--workflow", "linux.yml", "--branch", "main",
               "--commit", commit, "--limit", "1", "--json", "databaseId,status,conclusion,url"]
    for attempt in range(7):
        runs = json.loads(network(command, root))
        if runs:
            break
        if attempt < 6:
            time.sleep(10)
    if not runs:
        print(f"Linux CI has no main run for {repo} at {commit}", file=sys.stderr)
        return 1
    run = runs[0]
    print(f"Linux CI {repo} {commit[:12]}: {run['conclusion'] or run['status']} ({run['url']})", flush=True)
    if run["status"] == "completed":
        return int(run["conclusion"] != "success")
    return subprocess.run(["gh", "run", "watch", str(run["databaseId"]), "--repo", repo,
                           "--exit-status", "--interval", "15"], cwd=root).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "refresh-readme", "check", "publish", "ci"))
    parser.add_argument("plan", type=Path, help="JSON file containing candidate worktrees and versions")
    parser.add_argument("--only", action="append", choices=[app.replace("_", "-") for app in ORDER],
                        help="Run only the named application(s) from the plan")
    parser.add_argument("--confirm-user-approved", action="store_true",
                        help="Confirm approval of every selected candidate and its publication")
    parser.add_argument("--aur-maintainer", help="Also publish on AUR as this expected maintainer")
    parser.add_argument("--create-aur-package", action="append", default=[], metavar="PACKAGE")
    parser.add_argument("--allow-aur-downgrade", action="append", default=[], metavar="PACKAGE")
    args = parser.parse_args()
    if args.phase == "publish" and not args.confirm_user_approved:
        parser.error("Publication requires --confirm-user-approved")
    if args.phase != "publish" and (args.confirm_user_approved or args.aur_maintainer):
        parser.error("Approval and AUR flags apply to publish only")
    if (args.create_aur_package or args.allow_aur_downgrade) and not args.aur_maintainer:
        parser.error("AUR overrides require --aur-maintainer")

    plan_path = args.plan.resolve(strict=True)
    plan = load_plan(plan_path)
    selected = {app.replace("-", "_") for app in args.only} if args.only else set(plan)
    if selected - plan.keys():
        parser.error("--only must name an application in the plan")
    targets = {
        name for app in selected for name in aur_package_names(app.replace("_", "-"), plan[app][1]["version"])
    }
    if set(args.create_aur_package + args.allow_aur_downgrade) - targets:
        parser.error("AUR overrides must name a selected package")

    commands = []
    for app in ORDER:
        if app not in selected:
            continue
        root, entry = plan[app]
        version = entry["version"]
        if args.phase == "ci":
            commands.append((app.replace("_", "-"), root, []))
            continue
        command = ["--version", version]
        if args.phase in {"prepare", "refresh-readme"}:
            script = "prepare_release.py"
            if args.phase == "refresh-readme":
                if entry.get("skip_readme"):
                    continue
                command.append("--refresh-readme")
            elif not is_dev_version(version):
                command.append("--allow-stable")
        else:
            script = "publish_release.py"
            command.append("--dry-run" if args.phase == "check" else "--confirm-user-approved")
            if entry.get("skip_readme"):
                command.append("--skip-readme")
            if "notes" in entry:
                notes = (plan_path.parent / entry["notes"]).resolve(strict=True)
                if not notes.is_file():
                    raise ValueError(f"Expected a release notes file: {notes}")
                command.extend(["--notes-file", str(notes)])
            if args.aur_maintainer:
                command.extend(["--aur-maintainer", args.aur_maintainer])
                names = aur_package_names(app.replace("_", "-"), version)
                for flag, values in (("--create-aur-package", args.create_aur_package),
                                     ("--allow-aur-downgrade", args.allow_aur_downgrade)):
                    for name in values:
                        if name in names:
                            command.extend([flag, name])
        commands.append((app.replace("_", "-"), root, [sys.executable, str(SCRIPTS / script), *command]))

    completed = []
    for project, root, command in commands:
        print(f"==> {args.phase}: {project} ({root})", flush=True)
        result = watch_ci(root) if args.phase == "ci" else subprocess.run(command, cwd=root).returncode
        if result:
            print(f"{project} stopped with exit {result}. Completed: {', '.join(completed) or 'none'}.",
                  file=sys.stderr)
            return result
        completed.append(project)
    print(f"{args.phase} complete: {', '.join(completed) or 'no eligible applications'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
