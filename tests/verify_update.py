"""Self-update checks: config, throttled check, git-path apply guards, overlay, rollback.

The suite runs against the real self_update.py interface (branch/head based):
load_config/save_config, check(), apply_update(), rollback(), overlay().
"""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time as time_module
from unittest.mock import patch


SCRIPTS = Path(os.environ.get("LOOMHARNESS_REPO", Path(__file__).resolve().parents[1])) / "loomharness/scripts"


def load():
    spec = importlib.util.spec_from_file_location("harness_self_update_under_test", SCRIPTS / "self_update.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_config_roundtrip(home):
    with patch.dict(os.environ, CODEX_HOME=str(home)):
        updater = load()
        updater.save_config({"origin": "git-url", "branch": "main", "last_check": 1.0})
        cfg = updater.load_config()
        assert cfg["origin"] == "git-url" and cfg["branch"] == "main"
        # A torn write must never be read back as a valid partial config.
        assert json.loads(updater.config_path().read_text(encoding="utf8"))["origin"] == "git-url"


def test_check_throttles_remote_lookup(home, target):
    with patch.dict(os.environ, CODEX_HOME=str(target)):
        updater = load()
        updater.save_config({"origin": "git-url", "branch": "main"})
        calls = []
        with patch.object(updater, "skill_root", lambda: target), \
             patch.object(updater, "remote_head",
                          lambda origin, branch: calls.append((origin, branch)) or "sha"):
            cfg = updater.check(force=True)
            assert cfg["remote_head"] == "sha" and len(calls) == 1
            again = updater.check()
            assert again["remote_head"] == "sha" and len(calls) == 1  # within 24h: no network


def test_check_without_origin_reports_error(home, target):
    with patch.dict(os.environ, CODEX_HOME=str(target)):
        updater = load()
        with patch.object(updater, "skill_root", lambda: target):  # no .git and no configured origin
            cfg = updater.check(force=True)
        assert "error" in cfg and "No update source configured" in cfg["error"]


def make_git_skill(target):
    (target / ".git").mkdir(parents=True)
    (target / "VERSION").write_text("1.0.0\n", encoding="utf8")
    (target / "scripts").mkdir()
    (target / "scripts" / "tool.py").write_text("print('ok')\n", encoding="utf8")
    return target


def test_apply_rejects_modified_tracked_files(home, target):
    make_git_skill(target)
    with patch.dict(os.environ, CODEX_HOME=str(home)):
        updater = load()
        calls = []

        def fake_run_git(args, timeout=60):
            calls.append(args)
            if args[-1] == "--porcelain":
                return " M scripts/tool.py\n"
            return "git-url"
        with patch.object(updater, "skill_root", lambda: target), \
             patch.object(updater, "remote_head", lambda origin, branch: "remote-sha"), \
             patch.object(updater, "run_git", fake_run_git):
            try:
                updater.apply_update()
            except ValueError as exc:
                assert "uncommitted changes" in str(exc)
            else:
                raise AssertionError("expected ValueError for modified tracked files")
        assert not any("pull" in c for c in calls), "pull must not run on a dirty tree"


def test_apply_allows_untracked_files(home, target):
    make_git_skill(target)
    (target / "MY-NOTES.txt").write_text("user data\n", encoding="utf8")
    with patch.dict(os.environ, CODEX_HOME=str(home)):
        updater = load()
        states = iter(("before", "after"))

        def fake_run_git(args, timeout=60):
            if args[-1] == "--porcelain":
                return "?? MY-NOTES.txt\n"
            if args[-2:-1] == ["get-url"] or "--ff-only" in args:
                return "git-url"
            return next(states)
        with patch.object(updater, "skill_root", lambda: target), \
             patch.object(updater, "remote_head", lambda origin, branch: "remote-sha"), \
             patch.object(updater, "run_git", fake_run_git):
            message = updater.apply_update()
        assert "Updated" in message


def test_apply_rolls_back_on_broken_compile(home, target):
    import py_compile
    make_git_skill(target)
    with patch.dict(os.environ, CODEX_HOME=str(home)):
        updater = load()
        states = iter(("before", "after"))
        calls = []

        def fake_run_git(args, timeout=60):
            calls.append(args)
            if args[-1] == "--porcelain":
                return ""
            if args[-1] == "HEAD":
                return next(states)
            return "git-url"

        def broken(root):
            raise py_compile.PyCompileError(SyntaxError, SyntaxError("broken"), "tool.py")
        with patch.object(updater, "skill_root", lambda: target), \
             patch.object(updater, "remote_head", lambda origin, branch: "remote-sha"), \
             patch.object(updater, "run_git", fake_run_git), \
             patch.object(updater, "compile_check", broken):
            try:
                updater.apply_update()
            except ValueError as exc:
                assert "does not compile" in str(exc)
            else:
                raise AssertionError("expected ValueError on broken compile")
        reset = [c for c in calls if "reset" in c and "--hard" in c]
        assert reset and reset[0][-1] == "before", "rollback must restore the pre-pull HEAD"


def test_apply_success_updates_head(home, target):
    make_git_skill(target)
    with patch.dict(os.environ, CODEX_HOME=str(home)):
        updater = load()
        states = iter(("before", "after"))

        def fake_run_git(args, timeout=60):
            if args[-1] == "--porcelain":
                return ""
            if args[-2:-1] == ["get-url"] or "--ff-only" in args:
                return "git-url"
            return next(states)
        with patch.object(updater, "skill_root", lambda: target), \
             patch.object(updater, "remote_head", lambda origin, branch: "remote-sha"), \
             patch.object(updater, "run_git", fake_run_git):
            message = updater.apply_update()
        assert "Updated" in message
        cfg = updater.load_config()
        assert cfg["local_head"] == "after" and cfg["before_head"] == "before"


def test_overlay_keeps_untracked_top_level_files(target):
    updater = load()
    source = target.with_name("overlay-source")
    (source / "scripts").mkdir(parents=True)
    (source / "scripts" / "new.py").write_text("print('new')\n", encoding="utf8")
    (target / "scripts").mkdir(parents=True)
    (target / "scripts" / "old.py").write_text("print('old')\n", encoding="utf8")
    (target / "MY-NOTES.txt").write_text("user data\n", encoding="utf8")
    updater.overlay(source, target)
    assert (target / "scripts" / "new.py").is_file(), "new tracked file must appear"
    assert not (target / "scripts" / "old.py").exists(), "stale tracked file must go"
    assert (target / "MY-NOTES.txt").read_text(encoding="utf8") == "user data\n", "user file must survive"


def test_rollback_git_path(home, target):
    make_git_skill(target)
    with patch.dict(os.environ, CODEX_HOME=str(home)):
        updater = load()
        updater.save_config({"before_head": "abc123"})
        with patch.object(updater, "skill_root", lambda: target), \
             patch.object(updater, "run_git", lambda args, timeout=60: ""):
            message = updater.rollback()
        assert "abc123" in message


if __name__ == "__main__":
    base = Path(tempfile.mkdtemp())
    try:
        test_config_roundtrip(base / "codex-cfg")
        test_check_throttles_remote_lookup(base / "codex-throttle", base / "skill-throttle")
        test_check_without_origin_reports_error(base / "codex-noorigin", base / "skill-noorigin")
        test_apply_rejects_modified_tracked_files(base / "codex-dirty", base / "skill-dirty")
        test_apply_allows_untracked_files(base / "codex-untracked", base / "skill-untracked")
        test_apply_rolls_back_on_broken_compile(base / "codex-compile", base / "skill-compile")
        test_apply_success_updates_head(base / "codex-ok", base / "skill-ok")
        test_overlay_keeps_untracked_top_level_files(base / "skill-overlay")
        test_rollback_git_path(base / "codex-rb", base / "skill-rb")
    finally:
        deadline = time_module.monotonic() + 10
        while True:
            try:
                shutil.rmtree(base)
                break
            except PermissionError:
                if time_module.monotonic() >= deadline:
                    raise
                time_module.sleep(0.2)
    print("PASS: config roundtrip, throttled check, dirty tracked guard, untracked allowed, compile rollback, apply, overlay safety, rollback")
