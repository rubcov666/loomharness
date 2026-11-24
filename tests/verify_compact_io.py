"""Regression checks for compact I/O: output caps, review run logs, javac stamps, WAL, git-status cache."""

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import sqlite3
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

from verify_team import SCRIPTS, add, cli, init


def run_script(script, *args, cwd, env, ok=True, input=None):
    proc = subprocess.run([sys.executable, "-I", "-X", "utf8", str(script), *map(str, args)],
                          cwd=cwd, env=env, capture_output=True, encoding="utf8",
                          input=input, timeout=120)
    assert (proc.returncode == 0) == ok, proc
    return proc


def view_render_cap(root):
    big = root / "build.log"
    big.write_text("строка вывода\n" * 150000, encoding="utf8")
    proc = run_script(SCRIPTS / "view.py", big, cwd=root, env=os.environ)
    out = proc.stdout.encode("utf8")
    assert len(out) < 560000, len(out)
    assert "REVIEW" in proc.stdout and "--find" in proc.stdout
    assert big.read_text(encoding="utf8").startswith("строка вывода")
    small = root / "small.log"
    small.write_text("один\nдва\n", encoding="utf8")
    assert run_script(SCRIPTS / "view.py", small, cwd=root, env=os.environ).stdout == "один\nдва\n"


def read_line_cap(root, env):
    script = SCRIPTS / "harness.py"
    run_script(script, "enable", cwd=root, env=env)
    evidence = root / "проверка.log"
    evidence.write_text("короткая строка\n" + "Д" * 5000 + "\nещё строка\n", encoding="utf8")
    proc = run_script(script, "read", evidence.name, cwd=root, env=env)
    assert " […]" in proc.stdout and "частично" in proc.stdout
    assert "Д" * 440 not in proc.stdout and "короткая строка" in proc.stdout


def review_run_log(root):
    init(root, "--git", "none")
    manifest = root / "roles.json"
    manifest.write_text(json.dumps({"Notes": "Notes"}), encoding="utf8")
    cli(root, "lead", "organize", manifest, "--slots", 2)
    proof = root / "notes.txt"
    proof.write_text("4 records\n", encoding="utf8")
    revision = hashlib.sha256(proof.read_bytes()).hexdigest()
    task = add(root, "Notes", "Summarize the notes")
    brief = cli(root, "lead", "dispatch", task)["brief"]
    cli(root, "worker", "claim", task, brief["ticket"])
    cli(root, "worker", "submit", task, brief["ticket"], "notes.txt: four rows",
        "--artifact", proof.name, "--revision", revision)
    job = cli(root, "lead", "review", task)["brief"]
    cli(root, "reviewer", "job-claim", job["job"], job["ticket"])
    payload = "MARKER-" + "x" * 20000
    result = cli(root, "reviewer", "review-test", job["job"], job["ticket"], task, "--",
                 sys.executable, "-c", f"print({payload!r})")
    assert result["exit_code"] == 0
    log = Path(result["log"])
    assert log.is_file() and log.parent.name == "review-logs"
    assert payload in log.read_text(encoding="utf8")


def wal_mode(root, env):
    init(root)
    board = runpy.run_path(str(SCRIPTS / "harness.py"))["team_board"](root)
    with closing(sqlite3.connect(board)) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def javac_stamps(root):
    settings = os.fsdecode(subprocess.check_output(["java", "-XshowSettings:properties", "-version"],
                                                   stderr=subprocess.STDOUT))
    jdk = Path(re.search(r"(?m)^\s*java.home = (.+)$", settings).group(1).strip())
    release = re.search(r"(?m)^\s*java.specification.version = (\d+)", settings).group(1)
    src = root / "Flow.java"
    src.write_text("public class Flow { public static void main(String[] a) { } }", encoding="utf8")
    cmd = [sys.executable, "-I", "-X", "utf8", str(SCRIPTS / "javac_cache.py"),
           "--jdk", jdk, "--release", release, "--cache-dir", root / "cache", src]
    first = subprocess.run(cmd, cwd=root, capture_output=True)
    assert first.returncode == 0 and b"compiled" in first.stderr
    second = subprocess.run(cmd, cwd=root, capture_output=True)
    assert second.returncode == 0 and b"cache hit" in second.stderr
    entry = next((root / "cache").iterdir())
    ref = json.loads((entry / "current.json").read_text(encoding="utf8"))
    assert ref["files"] and all(":" in value for value in ref["files"].values())


def delegation_default(root, home):
    harness = runpy.run_path(str(SCRIPTS / "harness.py"))
    with patch.dict(os.environ, CODEX_HOME=str(home)):
        with closing(harness["open_preferences"]()) as db, db:
            hint = harness["session_hint"](db, root, "thread-default")
        assert harness["READER_MODEL"] in hint
        (home / "loomharness").mkdir(parents=True, exist_ok=True)
        (home / "loomharness" / "delegation.json").write_text(
            json.dumps({"read_review": "cheap-reader"}), encoding="utf8")
        with closing(harness["open_preferences"]()) as db, db:
            override = harness["session_hint"](db, root, "thread-default")
        assert "cheap-reader" in override and harness["READER_MODEL"] not in override


def git_status_cache(root, env):
    harness = runpy.run_path(str(SCRIPTS / "harness.py"))
    subprocess.run(["git", "init", "-q", "-b", "cache-check"], cwd=root, check=True,
                   capture_output=True)
    calls = []
    real_run = subprocess.run

    def counting(*args, **kwargs):
        calls.append(args[0])
        return real_run(*args, **kwargs)

    with patch.object(subprocess, "run", counting):
        first = harness["git_status"](root)
        assert harness["git_status"](root) == first and len(calls) == 1
        (root / "touch.txt").write_text("x", encoding="utf8")
        second = harness["git_status"](root)
        assert len(calls) == 2 and second != first and "Изменённых и новых файлов: 1" in second
        assert harness["git_status"](root) == second and len(calls) == 2


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as folder:
        base = Path(folder)
        with patch.dict(os.environ, CODEX_HOME=str(base / "codex")):
            view_render_cap(base)
            home = base / "home"
            home.mkdir()
            read_env = dict(os.environ, CODEX_HOME=str(base / "codex-read"),
                            CODEX_THREAD_ID="reader", TEMP=str(base), TMP=str(base), TMPDIR=str(base))
            read_line_cap(home, read_env)
            for name in ("review", "board", "javac", "git-cache"):
                (base / name).mkdir()
            review_run_log(base / "review")
            wal_mode(base / "board", os.environ)
            javac_stamps(base / "javac")
            delegation_default(base / "review", base / "codex-delegation")
            git_status_cache(base / "git-cache", os.environ)
    print("OK: render cap, read line cap, review run log, WAL board, javac stamps, git-status cache and delegation default")
