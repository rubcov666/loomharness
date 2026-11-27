"""One independent side review while a reproducible check runs outside the board lock."""

import argparse
from collections import deque
from contextlib import closing
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import time


def require(condition, message):
    if not condition:
        raise ValueError(message)


def arguments(cmds, text, seconds):
    test = cmds.add_parser("review-test", help="run a candidate check and notify the board on exit")
    test.add_argument("--recheck", type=seconds, default=300, help="seconds before coordinator checks the runner")
    for name, parser in (
        ("test", test),
        ("side", cmds.add_parser("review-side")),
        ("interrupt", cmds.add_parser("review-interrupt")),
    ):
        parser.add_argument("id", type=int, help="active review job")
        if name != "side":
            parser.add_argument("ticket", type=text)
        if name != "interrupt":
            parser.add_argument("task", type=int)
        else:
            parser.add_argument("run", type=text)
            parser.add_argument("note", type=text)
    test.add_argument("command", nargs=argparse.REMAINDER)


def exists(db, table):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()


def mark_short(db, task, revision, short):
    if short:
        db.execute(
            "CREATE TABLE IF NOT EXISTS short_reviews(task INTEGER PRIMARY KEY REFERENCES tasks, revision TEXT NOT NULL)"
        )
        db.execute("INSERT OR REPLACE INTO short_reviews VALUES(?,?)", (task, revision))
    elif exists(db, "short_reviews"):
        db.execute("DELETE FROM short_reviews WHERE task=?", (task,))


def current(db, job):
    if exists(db, "review_runs"):
        row = db.execute("SELECT * FROM review_runs WHERE job=?", (job,)).fetchone()
        return dict(row) if row else None


def candidates(db, run):
    if run["status"] != "running" or time.time() >= run["due"] or run["side"] or not exists(db, "short_reviews"):
        return []
    # Unknown scopes and the project-wide scope both require exclusive access.
    return [
        r[0]
        for r in db.execute(
            """SELECT t.id FROM tasks t JOIN submissions s ON s.task=t.id
        JOIN short_reviews q ON q.task=t.id AND q.revision=s.revision
        WHERE t.state='review' AND EXISTS (SELECT 1 FROM resources WHERE task=t.id)
          AND NOT EXISTS (SELECT 1 FROM job_tasks x WHERE x.job=? AND
             (x.task=t.id OR NOT EXISTS (SELECT 1 FROM resources WHERE task=x.task) OR
              EXISTS (SELECT 1 FROM resources a JOIN resources b
                ON a.name='*' OR b.name='*' OR a.name=b.name WHERE a.task=x.task AND b.task=t.id)))
        ORDER BY t.id""",
            (run["job"],),
        )
    ]


def review_owner(db, job):
    if exists(db, "native_services"):
        native = db.execute("SELECT actor FROM native_services WHERE job=?", (job,)).fetchone()
        if native is not None:
            return native[0], True
    owner = db.execute("SELECT thread FROM chats WHERE slot='service/review'").fetchone()
    return (owner[0] if owner else None), False


def side(db, a, model, thinking):
    run = current(db, a.id)
    require(run and a.task in candidates(db, run), "no independent short review available in this run")
    card = db.execute(
        """SELECT t.id,title,role,scope,criterion,evidence,revision,artifact FROM tasks t
                         JOIN submissions s ON s.task=t.id WHERE t.id=?""",
        (a.task,),
    ).fetchone()
    db.execute("INSERT INTO job_tasks VALUES(?,?)", (a.id, a.task))
    db.execute("INSERT INTO review_items(job,task,revision) VALUES(?,?,?)", (a.id, a.task, card["revision"]))
    db.execute("UPDATE review_runs SET side=? WHERE job=?", (a.task, a.id))
    owner, native = review_owner(db, a.id)
    require(owner, "reviewer must claim the current job")
    return {
        "operation": "send_input" if native else "send_message_to_thread",
        "threadId": owner,
        "model": model,
        "thinking": thinking,
        "brief": {"job": a.id, "kind": "review", "card": dict(card), "already_claimed": True},
    }


def summarize_run(log_path, code, stderr=None):
    if stderr is None:
        stderr = sys.stderr
    # The chat receives a bounded digest; the full bytes stay in the log file.
    # Stream the file: a build log can be hundreds of megabytes, and the digest
    # needs only the first non-blank lines and the tail.
    print(f"Full log: {log_path}", file=stderr)
    first = []
    tail = deque(maxlen=3)
    total = 0
    try:
        with log_path.open(encoding="utf-8-sig", errors="backslashreplace") as stream:
            for raw in stream:
                line = raw.strip()
                if not line:
                    continue
                total += 1
                if len(first) < 2:
                    first.append(line)
                tail.append(line)
    except OSError:
        return
    print(f"exit={code} | lines={total}", file=stderr)
    picked = first + (["…"] if total > 5 else []) + list(tail)
    for line in picked:
        print(line[:240], file=stderr)


def run_test(path, root, a, actor, verify, *, stdout=None, stderr=None):
    command = a.command[1:] if a.command[:1] == ["--"] else a.command
    require(command, "provide a test executable and its arguments")
    with closing(sqlite3.connect(path, timeout=8)) as db:
        db.row_factory = sqlite3.Row
        with db:
            db.execute("BEGIN IMMEDIATE")
            require(
                not (root / "AGENT_STOP").exists() and not db.execute("SELECT paused FROM project").fetchone()[0],
                "project is stopped or paused",
            )
            owner, _ = review_owner(db, a.id)
            job = db.execute(
                "SELECT * FROM jobs WHERE id=? AND kind='review' AND state='active'", (a.id,)
            ).fetchone()
            require(
                owner and actor == owner and job and job["ticket"] == a.ticket,
                "active reviewer ticket required",
            )
            row = db.execute(
                """SELECT r.revision,s.artifact FROM review_items r JOIN submissions s
                ON s.task=r.task AND s.revision=r.revision WHERE r.job=? AND r.task=? AND r.decision IS NULL""",
                (a.id, a.task),
            ).fetchone()
            require(row, "select a pending revision in this review")
            db.execute("""CREATE TABLE IF NOT EXISTS review_runs(job INTEGER PRIMARY KEY REFERENCES jobs,
                run TEXT NOT NULL, task INTEGER NOT NULL, status TEXT NOT NULL, due REAL NOT NULL,
                side INTEGER, exit_code INTEGER, note TEXT)""")
            old = current(db, a.id)
            require(not old or old["status"] != "running", "a test is already running")
            if old:
                pending = db.execute(
                    "SELECT 1 FROM review_items WHERE job=? AND task=? AND decision IS NULL", (a.id, old["task"])
                ).fetchone()
                require(
                    not pending or old["task"] == a.task or old["status"] == "passed",
                    "resolve the failed candidate before testing another card",
                )
                if old["side"]:
                    require(
                        not db.execute(
                            "SELECT 1 FROM review_items WHERE job=? AND task=? AND decision IS NULL",
                            (a.id, old["side"]),
                        ).fetchone(),
                        "finish the side review first",
                    )
            run = secrets.token_hex(12)
            db.execute(
                "INSERT OR REPLACE INTO review_runs VALUES(?,?,?,'running',?,NULL,NULL,NULL)",
                (a.id, run, a.task, time.time() + a.recheck),
            )
        # Give the test normal project inputs, without the reviewer's chat identity.
        env = os.environ.copy()
        env.pop("CODEX_THREAD_ID", None)
        code = None
        log_path = None
        try:
            verify(db, root, row["revision"], row["artifact"])
            require(
                not (root / "AGENT_STOP").exists() and not db.execute("SELECT paused FROM project").fetchone()[0],
                "project is stopped or paused",
            )
            active = current(db, a.id)
            require(
                active and active["run"] == run and active["status"] == "running",
                "test was reconciled before launch",
            )
            log_dir = path.parent / "review-logs"
            log_dir.mkdir(exist_ok=True)
            log_path = log_dir / f"{run}.log"
            with log_path.open("xb") as log:
                code = subprocess.run(command, cwd=root, env=env,
                                      stdout=log, stderr=subprocess.STDOUT).returncode
        finally:
            with db:
                recorded = db.execute(
                    "UPDATE review_runs SET status=?,exit_code=? WHERE job=? AND run=? AND status='running'",
                    ("passed" if code == 0 else "failed", code, a.id, run),
                ).rowcount
    require(recorded, "test run already reconciled; inspect the current review")
    if log_path is not None:
        summarize_run(log_path, code, stderr)
    print(json.dumps({"job": a.id, "task": a.task, "run": run, "exit_code": code,
                      **({"log": str(log_path)} if log_path is not None else {})}), file=stdout)
    return 0 if code == 0 else 1
