"""Headless drivers: fake CLI binaries on PATH, model detection, board wiring."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


SCRIPTS = Path(os.environ.get("LOOMHARNESS_REPO", Path(__file__).resolve().parents[1])) / "loomharness" / "scripts"
FAKE_CLAUDE = """import json, sys
prompt = sys.argv[sys.argv.index("-p") + 1]
print(json.dumps({"result": "done", "total_cost_usd": 0.01,
                  "usage": {"claude-sonnet": {"input_tokens": 10, "output_tokens": 5}}}))
print("PROMPT_WAS:" + prompt[:400].replace("\\n", " | "), file=sys.stderr)
"""
FAKE_GEMINI = """import json, sys
prompt = sys.argv[sys.argv.index("-p") + 1]
print(json.dumps({"response": "ok", "stats": {"prompt_tokens": 7, "candidates_tokens": 3}}))
"""


def driver(env, *args, ok=True):
    proc = subprocess.run([sys.executable, "-I", "-X", "utf8", str(SCRIPTS / "team_driver.py"), *map(str, args)],
                          capture_output=True, encoding="utf8", env=env, timeout=60)
    if ok:
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout) if proc.stdout.strip() else proc.stdout
    assert proc.returncode, proc.stdout
    return proc.stderr


def team(env, project, actor, *args, ok=True):
    proc = subprocess.run([sys.executable, "-I", "-X", "utf8", str(SCRIPTS / "team.py"),
                           "--project", str(project), *map(str, args)],
                          capture_output=True, encoding="utf8", env=env, timeout=30)
    if ok:
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)
    assert proc.returncode, proc.stdout
    return proc.stderr


def fake_cli(binary, body):
    """Create an executable fixture CLI with a Windows shim or POSIX shebang."""
    script = binary.with_suffix(".py")
    script.write_text(body, encoding="utf8")
    if os.name == "nt":
        binary.with_suffix(".cmd").write_text(f'@echo off\n"{sys.executable}" "{script}" %*\n', encoding="utf8")
    else:
        binary.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf8")
        binary.chmod(0o755)


with tempfile.TemporaryDirectory() as folder:
    base = Path(folder).resolve()
    env = dict(os.environ, CODEX_HOME=str(base / "codex"), CODEX_THREAD_ID="lead")
    root = base / "project"
    root.mkdir()

    err = driver(env, "run", "--cli", "claude", "--project", str(root), "--task", "1", "--ticket", "x", ok=False)
    assert "no team board" in err

    team(env, root, "lead", "init", "--goal", "Ship it", "--simple-model", "m1",
         "--strong-model", "m2", "--hours-per-role", "1")
    task_id = team(env, root, "lead", "add", "--role", "dev", "--title", "Fix import",
                   "--scope", "a.txt", "--criterion", "tests pass", "--hours", "0.5")["id"]

    # Desktop is the default driver. Exercise selecting and resetting it.
    assert team(env, root, "lead", "driver", "show")["driver"] == "desktop"
    assert team(env, root, "lead", "driver", "set", "claude")["driver"] == "claude"
    assert team(env, root, "lead", "driver", "show")["driver"] == "claude"
    assert team(env, root, "lead", "driver", "set", "desktop")["driver"] == "desktop"

    # A missing executable must give an actionable error and leave the board intact.
    empty_bin = base / "empty-bin"
    empty_bin.mkdir()
    env_no_cli = dict(env, PATH=str(empty_bin) + os.pathsep + str(base / "no-python"))
    message = team(env_no_cli, root, "lead", "driver", "set", "claude")
    message = team(env_no_cli, root, "lead", "dispatch", task_id, ok=False)
    assert "not found on PATH" in message
    team(env_no_cli, root, "lead", "driver", "set", "desktop")

    # Fixture drivers expose full command arguments through dispatch.
    fake_bin = base / "bin"
    fake_bin.mkdir()
    fake_cli(fake_bin / "claude", FAKE_CLAUDE)
    fake_cli(fake_bin / "gemini", FAKE_GEMINI)
    env_cli = dict(env, PATH=str(fake_bin) + os.pathsep + os.environ.get("PATH", ""))

    team(env_cli, root, "lead", "driver", "set", "claude")
    result = team(env_cli, root, "lead", "dispatch", task_id, "--reply-language", "ru")
    assert result["operation"] == "run_command", result
    assert result["brief"]["reply_language"] == "ru"
    cmd = [str(c) for c in result["command"]]
    assert cmd[cmd.index("--cli") + 1] == "claude"
    assert cmd[cmd.index("--task") + 1] == str(task_id)
    assert cmd[cmd.index("--project") + 1] == str(root)

    # The driver reads JSON usage and records the card prompt in the log.
    proc = subprocess.run(cmd, capture_output=True, encoding="utf8", env=env_cli, timeout=60)
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["ok"] and report["cli"] == "claude"
    assert report["usage"]["cost_usd"] == 0.01
    text = Path(report["log"]).read_text(encoding="utf8")
    assert "Fix import" in text and "PROMPT_WAS:" in text and "tests pass" in text
    assert "User-facing output language: ru." in text
    assert (root / ".loomharness" / "runs").is_dir()

    # Reject a bad ticket and release the first card for the second run.
    err = driver(env_cli, "run", "--cli", "claude", "--project", str(root),
                 "--task", str(task_id), "--ticket", "wrong", ok=False)
    assert "invalid dispatch ticket" in err
    team(env_cli, root, "lead", "release", task_id, "driver run verified")

    # The Gemini driver parses stats on a separately dispatched card.
    task2 = team(env_cli, root, "lead", "add", "--role", "qa", "--title", "Fix export",
                 "--scope", "b.txt", "--criterion", "export works", "--hours", "0.5")["id"]
    team(env_cli, root, "lead", "driver", "set", "gemini")
    cmd = [str(c) for c in team(env_cli, root, "lead", "dispatch", task2)["command"]]
    proc = subprocess.run(cmd, capture_output=True, encoding="utf8", env=env_cli, timeout=60)
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["ok"] and report["usage"]["stats"]["prompt_tokens"] == 7
    team(env_cli, root, "lead", "release", task2, "gemini driver run verified")

    # Discover Claude aliases and persist the routine model with --apply.
    models = driver(env_cli, "models", "--project", str(root))
    assert models["claude"]["present"] and "sonnet" in models["claude"]["models"]
    assert models["gemini"]["present"]
    driver(env_cli, "models", "--project", str(root), "--apply", "--routine", "m1-fast")
    saved = json.loads((root / ".codex" / "harness-models.json").read_text(encoding="utf8"))
    assert saved == {"routine": "m1-fast"}
    err = driver(env_cli, "models", "--project", str(root), "--apply", "--routine", "bad name!", ok=False)
    assert "Supply --routine" in err

    # Switching to Desktop restores normal dispatch.
    team(env_cli, root, "lead", "driver", "set", "desktop")
    result = team(env_cli, root, "lead", "dispatch", task_id, "--reply-language", "fr")
    assert result["operation"] == "create_thread", result
    assert result["brief"]["reply_language"] == "fr"

    # A grandchild outlives the timeout unless the full process tree is killed.
    err = driver(env_cli, "run", "--cli", "claude", "--project", str(root),
                 "--task", str(task_id), "--ticket", "x", "--timeout", "1", ok=False)
    assert "invalid dispatch ticket" in err  # The board rejects it before launch.
    team(env_cli, root, "lead", "release", task_id, "round 2")
    team(env_cli, root, "lead", "driver", "set", "claude")
    cmd = [str(c) for c in team(env_cli, root, "lead", "dispatch", task_id)["command"]]
    cmd += ["--timeout", "1"]
    # Put the slow executable first on PATH for this timeout case.
    sleepy_bin = base / "sleepy-bin"
    sleepy_bin.mkdir()
    fake_cli(sleepy_bin / "claude", "import subprocess, sys, time\n"
             "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
             "time.sleep(60)\n")
    env_sleepy = dict(env_cli, PATH=str(sleepy_bin) + os.pathsep + env_cli["PATH"])
    start = time.time()
    proc = subprocess.run(cmd, capture_output=True, encoding="utf8", env=env_sleepy, timeout=90)
    elapsed = time.time() - start
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["exit"] == 124 and report["seconds"] < 30, report
    assert elapsed < 30, elapsed

print("PASS: driver run/usage parsing, ticket validation, board wiring, PATH guard, model detection and apply")
