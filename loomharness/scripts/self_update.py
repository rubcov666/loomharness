"""Quiet self-update for the installed loomharness copy.

Configuration: $CODEX_HOME/loomharness/self-update.json
    {"origin": "git-url", "branch": "main", "last_check": epoch,
     "remote_head": sha, "local_head": sha, "backup": "path"}
Remote version checks are cached for 24 hours. A routine update check leaves
the installation untouched. Applying an update waits for all board assignments
to finish. Git installations fast-forward. Plain directories use a checked
clone, compilation, overlay and backup.
"""

import json
import os
import py_compile
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    import hk
except ImportError:  # python -I excludes the script directory from sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import hk


CHECK_INTERVAL = 24 * 3600
TRACKED_TOP = ("SKILL.md", "README.md", "VERSION", "LICENSE", "scripts", "references", "agents")


def data_directory():
    return hk.kit_home()


def skill_root():
    return Path(__file__).resolve().parent.parent


def config_path():
    return data_directory() / "self-update.json"


def load_config():
    try:
        raw = json.loads(config_path().read_text(encoding="utf8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    data_directory().mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf8", dir=config_path().parent,
                                     delete=False) as handle:
        json.dump(cfg, handle, ensure_ascii=False, indent=1)
        temp = Path(handle.name)
    temp.replace(config_path())


def local_version():
    try:
        return (skill_root() / "VERSION").read_text(encoding="utf8").strip()
    except OSError:
        return "unknown"


def git_exe():
    return shutil.which("git")


def run_git(args, timeout=60):
    git = git_exe()
    if not git:
        raise ValueError("Git not found on PATH")
    result = subprocess.run([git, *args], capture_output=True, encoding="utf8", timeout=timeout)
    if result.returncode:
        raise ValueError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def detect_origin():
    if (skill_root() / ".git").is_dir():
        try:
            return run_git(["-C", str(skill_root()), "remote", "get-url", "origin"]).strip() or None
        except ValueError:
            return None


def remote_head(origin, branch):
    git = git_exe()
    if not git:
        raise ValueError("Git not found on PATH")
    result = subprocess.run([git, "ls-remote", origin, branch], capture_output=True, encoding="utf8", timeout=30)
    if result.returncode:
        return None
    fields = result.stdout.split()
    return fields[0] if fields else None


def any_active_work():
    profile = hk.kit_home(Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex"))
    boards = {p.resolve() for base in (data_directory(), profile) for p in (base / "teams").glob("*.sqlite3")}
    for board in sorted(boards):
        try:
            with sqlite3.connect(board.as_uri() + "?mode=ro", uri=True, timeout=2) as db:
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "tasks" in tables and db.execute(
                        "SELECT 1 FROM tasks WHERE state IN ('reserved','working') LIMIT 1").fetchone():
                    return True
                if "jobs" in tables and db.execute(
                        "SELECT 1 FROM jobs WHERE state IN ('offered','active') LIMIT 1").fetchone():
                    return True
        except sqlite3.Error:
            continue
    return False


def compile_check(root):
    for path in sorted((root / "scripts").glob("*.py")):
        py_compile.compile(str(path), doraise=True)


def check(force=False):
    # Cache version queries for a day. A check leaves skill files unchanged.
    cfg = load_config()
    now = time.time()
    if not force and now - cfg.get("last_check", 0) < CHECK_INTERVAL and cfg.get("remote_head"):
        return cfg
    origin = cfg.get("origin") or detect_origin()
    if not origin:
        return dict(cfg, error="No update source configured: harness.py update --set-origin URL [--branch main]")
    branch = cfg.get("branch", "main")
    head = remote_head(origin, branch)
    if not head:
        return dict(cfg, error=f"Could not read {branch} from {origin}")
    cfg.update(origin=origin, branch=branch, last_check=now, remote_head=head)
    save_config(cfg)
    return cfg


def backup_current(root):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = data_directory() / "backups" / stamp

    def ignore(directory, names):
        return {n for n in names if n == "__pycache__"}

    shutil.copytree(root, target, ignore=ignore)
    cfg = load_config()
    cfg["backup"] = str(target)
    save_config(cfg)
    return target


def overlay(source, target):
    # Replace files in place because a running process may use the directory.
    # Overlay files from the source and remove obsolete tracked entries.
    # Restrict removals to TRACKED_TOP so user notes and other files outside
    # the managed paths survive.
    source_files = {p.relative_to(source) for p in source.rglob("*") if p.is_file()}
    for rel in source_files:
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / rel, dest)
    for entry in target.iterdir():
        if entry.name == ".git":
            continue
        if entry.name not in TRACKED_TOP:
            continue
        probe = source / entry.name
        if entry.is_file():
            if not probe.is_file():
                entry.unlink()
            continue
        if not probe.is_dir():
            shutil.rmtree(entry)
            continue
        for path in sorted(entry.rglob("*"), reverse=True):
            if path.is_file() and not (probe / path.relative_to(entry)).exists():
                path.unlink()
        for path in sorted((p for p in entry.rglob("*") if p.is_dir()), reverse=True):
            if not any(path.iterdir()):
                path.rmdir()


def apply_update():
    if any_active_work():
        raise ValueError("Active board assignments exist. Update deferred.")
    cfg = check(force=True)
    if cfg.get("error"):
        raise ValueError(cfg["error"])
    origin, branch = cfg["origin"], cfg.get("branch", "main")
    root = skill_root()
    if (root / ".git").is_dir():
        # Rollback resets tracked files, so reject local tracked edits.
        # Untracked files, such as user notes and __pycache__, are outside
        # the reset's managed file set.
        status = run_git(["-C", str(root), "status", "--porcelain"]).splitlines()
        dirty = [line for line in status if not line.startswith(("??", "!!"))]
        if dirty:
            raise ValueError("The skill directory has uncommitted changes in tracked files. "
                             "Commit or set them aside before updating.")
        before = run_git(["-C", str(root), "rev-parse", "HEAD"]).strip()
        run_git(["-C", str(root), "pull", "--ff-only", "origin", branch])
        after = run_git(["-C", str(root), "rev-parse", "HEAD"]).strip()
        if before == after:
            return "Already up to date"
        try:
            compile_check(root)
        except py_compile.PyCompileError:
            # Tracked files were clean before pull. Restore those files to
            # the recorded pre-update commit.
            run_git(["-C", str(root), "reset", "--hard", before])
            raise ValueError("The new version does not compile. Git rollback completed.")
        cfg["local_head"] = after
        cfg["before_head"] = before
        save_config(cfg)
        return f"Updated {before[:8]}..{after[:8]} (version {local_version()})"
    with tempfile.TemporaryDirectory() as tmp:
        clone = Path(tmp) / "skill"
        run_git(["clone", "--depth", "1", "--branch", branch, origin, str(clone)], timeout=300)
        compile_check(clone)
        backup = backup_current(root)
        overlay(clone, root)
    cfg = load_config()
    cfg["local_head"] = cfg.get("remote_head")
    save_config(cfg)
    return f"Updated from {origin} (version {local_version()}). Previous copy: {backup}"


def rollback():
    root = skill_root()
    cfg = load_config()
    if (root / ".git").is_dir():
        before = cfg.get("before_head")
        if not before:
            raise ValueError("No previous commit recorded")
        run_git(["-C", str(root), "reset", "--hard", before])
        return f"Rolled back to {before[:8]} (version {local_version()})"
    backup = cfg.get("backup")
    if not backup or not Path(backup).is_dir():
        raise ValueError("No backup available")
    overlay(Path(backup), root)
    return f"Restored from {backup} (version {local_version()})"


def main(args):
    if args.set_origin:
        cfg = load_config()
        cfg["origin"] = args.set_origin
        if args.branch:
            cfg["branch"] = args.branch
        save_config(cfg)
        print(f"Update source: {cfg['origin']} ({cfg.get('branch', 'main')})")
        return 0
    if args.rollback:
        print(rollback())
        return 0
    if args.apply:
        print(apply_update())
        return 0
    cfg = check(force=args.force_check)
    if cfg.get("error"):
        print(cfg["error"])
        return 1
    local = run_git(["-C", str(skill_root()), "rev-parse", "HEAD"],
                    ).strip() if (skill_root() / ".git").is_dir() else cfg.get("local_head")
    if local == cfg.get("remote_head"):
        print(f"Up to date (version {local_version()})")
        return 0
    print(f"New version available. Installed: {local_version()}. Apply with: harness.py update --apply")
    return 0


def add_parser(commands):
    update = commands.add_parser("update", help="quiet self-update: check, apply, rollback")
    update.add_argument("--set-origin", metavar="URL", help="git URL of the loomharness repository")
    update.add_argument("--branch", help="branch to track (default: main)")
    update.add_argument("--force-check", action="store_true", help="ignore the 24h check cache")
    update.add_argument("--apply", action="store_true", help="apply an available update")
    update.add_argument("--rollback", action="store_true", help="restore the previous version")


if __name__ == "__main__":
    class A:
        set_origin = branch = None
        force_check = apply = rollback = False
    sys.exit(main(A()))
