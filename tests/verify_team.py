"""Exercise the public board CLI in isolated local projects without calling a model."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import time


SCRIPTS = Path(os.environ.get("LOOMHARNESS_REPO", Path(__file__).resolve().parents[1])) / "loomharness" / "scripts"


def cli(project, actor, *args, fail=False):
    env = dict(os.environ, CODEX_HOME=str(project.parent / "codex"), CODEX_THREAD_ID=actor)
    proc = subprocess.run([sys.executable, "-I", "-X", "utf8", str(SCRIPTS / "team.py"),
                           "--project", str(project), *map(str, args)], env=env,
                          capture_output=True, encoding="utf8", timeout=20)
    if fail:
        assert proc.returncode and "Traceback" not in proc.stderr, proc
        return proc.stderr
    assert proc.returncode == 0, proc
    return json.loads(proc.stdout)


def init(root, *opts):
    return cli(root, "lead", "init", "--goal", "Ship the reviewed catalogue",
               "--simple-model", "fast-local", "--strong-model", "capable-local", *opts)


def add(root, role, title, *opts):
    return cli(root, "lead", "add", "--role", role, "--title", title, "--scope", f"{role}.txt",
               "--criterion", "matches source counts", "--hours", "0.5", *opts)["id"]


def deliver(root, idx, dispatch, worker):
    brief = dispatch["brief"]
    cli(root, worker, "claim", idx, brief["ticket"])
    (root / brief["scope"]).write_text("4 records\n", encoding="utf8")
    cli(root, worker, "submit", idx, brief["ticket"], f'{brief["scope"]}: four rows')


def indexed_log(home, thread, start, tokens, duration, *, pending=False):
    path = home / (thread + ".jsonl")
    def event(kind, **args):
        return {"type": "event_msg", "payload": dict(type=kind, **args)}
    def response(tid, key):
        return {"type": "token_usage_record", "payload": {"turn_id": tid, "thread_id": thread,
                "response_id": key, "usage": dict(zip(("input_tokens", "cached_input_tokens", "output_tokens"), tokens))}}
    rows = [event("task_started", turn_id="old", started_at=start - 100),
            response("old", "prior"), event("task_complete", turn_id="old", completed_at=start-90, duration_ms=10000),
            event("task_started", turn_id="current", started_at=start),
            response("current", "answer"), response("current", "answer"),
            event("task_complete", turn_id="current", completed_at=start+10, duration_ms=duration)]
    if pending:
        rows.append(event("task_started", turn_id="running", started_at=start))
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf8")
    with closing(sqlite3.connect(home / "state_5.sqlite")) as index, index:
        index.execute("CREATE TABLE IF NOT EXISTS threads(id TEXT PRIMARY KEY, rollout_path TEXT)")
        index.execute("INSERT OR REPLACE INTO threads VALUES(?,?)", (thread, str(path)))


def role_project(root):
    root.mkdir()
    assert "no team board" in cli(root, "lead", "plan", fail=True)
    init(root, "--hours-per-role", 1)
    assert cli(root, "lead", "intake")["actor"] == "lead"
    existing = cli(root, "visitor", "intake")
    assert existing["actor"] == "other" and existing["leadThreadId"] == "lead" and "project_notes" not in existing
    assert "already initialized" in cli(root, "other", "init", "--goal", "x", "--simple-model", "s",
                                        "--strong-model", "x", fail=True)
    a = add(root, "catalog", "count")
    b = add(root, "catalog", "verify count", "--after", a)
    assert "exceeds" in cli(root, "lead", "edit", b, "--hours", 0.75, fail=True)
    assert "earlier" in cli(root, "lead", "edit", a, "--after", b, fail=True)
    assert "prerequisite" in cli(root, "lead", "dispatch", b, fail=True)
    assert "only the project lead" in cli(root, "worker", "dispatch", a, fail=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        def reserve(_):
            try:
                return cli(root, "lead", "dispatch", a)
            except AssertionError as exc:
                assert "already dispatched" in str(exc), exc
                return None
        dispatch, = (result for result in pool.map(reserve, range(2)) if result)
    assert dispatch["operation"] == "create_thread" and dispatch["model"] == "fast-local"
    assert Path(dispatch["brief"]["project"]) == root.resolve()
    cli(root, "lead", "release", a, "delivery confirmed absent")
    cli(root, "worker", "claim", a, dispatch["brief"]["ticket"], fail=True)
    dispatch = cli(root, "lead", "dispatch", a)
    cli(root, "lead", "bind", a, dispatch["brief"]["ticket"], "worker")
    cli(root, "stranger", "claim", a, dispatch["brief"]["ticket"], fail=True)
    cli(root, "lead", "accept", a, "premature", fail=True)
    deliver(root, a, dispatch, "worker")
    intake = cli(root, "worker", "intake")
    assert intake["actor"] == "worker" and intake["slot"] == "role/catalog"
    cli(root, "worker", "accept", a, "self-review", fail=True)
    cli(root, "lead", "revise", a, "missing blank-row case")
    revision = cli(root, "lead", "dispatch", a, "--tier", "strong")
    assert revision["threadId"] == "worker" and revision["model"] == "capable-local"
    assert "blank-row" in revision["brief"]["feedback"]
    deliver(root, a, revision, "worker")
    cli(root, "lead", "accept", a, "recounted with the blank row")
    next_card = cli(root, "lead", "dispatch", b)
    assert next_card["operation"] == "send_message_to_thread" and next_card["threadId"] == "worker"
    assert next_card["brief"]["project"] == dispatch["brief"]["project"]
    assert next_card["brief"]["prerequisites"][0]["id"] == a
    prior = next_card["brief"]["prerequisites"][0]
    assert prior["evidence"] is None and prior["review"] == "recounted with the blank row"
    assert cli(root, "worker", "show", a)["evidence"]
    deliver(root, b, next_card, "worker")
    cli(root, "lead", "accept", b, "opened output and compared to source")
    plan = cli(root, "lead", "plan")
    assert [card["returns"] for card in plan["tasks"]] == [1, 0]
    no_logs = cli(root, "lead", "usage")
    assert no_logs["measured"] is None and no_logs["missing_chats"] == ["lead", "worker"]
    now = int(time.time())
    home = root.parent / "codex"
    indexed_log(home, "lead", now, (120, 40, 30), 3600)
    indexed_log(home, "worker", now, (100, 50, 10), 3600, pending=True)
    interim = cli(root, "lead", "usage")
    assert interim["unfinished_turns"] == 1 and interim["project_tokens_per_accepted_task"] is None
    indexed_log(home, "worker", now, (100, 50, 10), 3600)
    final = cli(root, "lead", "usage")
    assert final["complete"] and final["project_tokens_per_accepted_task"] == 130
    assert final["by_role"] == [{"role": "catalog", "planned_hours": 1.0, "accepted": 2, "revisions": 1,
                                "measured": {"tokens": 110, "agent_hours": 0.001, "tokens_per_agent_hour": 110000}}]
    assert final["measured"] == {"input": 220, "cached_input": 90, "uncached_input": 130, "cache_hit_ratio": 0.4091,
                                 "output": 40, "total": 260, "effective_cost": 299,
                                 "lead_tokens": 150, "worker_tokens": 110, "agent_hours": 0.002,
                                 "tokens_per_agent_hour": 130000}
    (root / "AGENT_STOP").touch()
    assert "AGENT_STOP" in cli(root, "lead", "pause", fail=True)
    assert cli(root, "worker", "plan") == plan
    (root / "AGENT_STOP").unlink()
    cli(root, "lead", "pause")
    cli(root, "lead", "edit", b, "--hours", 0.01, fail=True)
    cli(root, "lead", "resume")
    assert not cli(root, "lead", "plan")["paused"]


def other_modes(root, mode):
    root.mkdir()
    init(root, "--mode", mode)
    a = add(root, "prepare", "write")
    b = add(root, "review" if mode == "pipeline" else "prepare", "check", "--after", a)
    if mode == "pipeline":
        assert "different owners" in cli(root, "lead", "edit", b, "--role", "prepare", fail=True)
    first = cli(root, "lead", "dispatch", a)
    other = add(root, "layout", "preview")
    assert "slots" in cli(root, "lead", "dispatch", other, fail=True)
    deliver(root, a, first, "author")
    cli(root, "lead", "accept", a, "file verified")
    second = cli(root, "lead", "dispatch", b)
    assert second["operation"] == "create_thread" and second["threadId"] is None
    assert second["brief"]["prerequisites"][0]["evidence"]  # fresh assignee receives source evidence
    deliver(root, b, second, "checker")
    cli(root, "lead", "bind", b, second["brief"]["ticket"], "checker")
    cli(root, "lead", "accept", b, "comparison verified")


def handoff_usage(root):
    root.mkdir()
    init(root)
    cli(root, "lead", "handoff", "next")
    cli(root, "lead", "handoff", "next")
    home = root.parent / "codex"
    board = next((home / "loomharness/teams").glob("*.sqlite3"))
    with closing(sqlite3.connect(board)) as db, db:
        assert db.execute("SELECT count(*) FROM lead_handoffs").fetchone()[0] == 1
        db.execute("UPDATE windows SET start=100")
        db.execute("UPDATE project SET created=100")
        db.execute("UPDATE lead_handoffs SET at=200")
    # Both chats worked before and after the transfer. Only one turn per lead belongs to this board.
    indexed_log(home, "lead", 250, (10, 0, 1), 1000)
    indexed_log(home, "next", 250, (20, 0, 2), 1000)
    report = cli(root, "next", "usage")
    assert report["complete"] and report["completed_measured_turns"] == 2, report
    assert report["measured"]["lead_tokens"] == 33 and report["measured"]["worker_tokens"] == 0
    with closing(sqlite3.connect(board)) as db, db:
        db.execute("UPDATE windows SET stop=140")
        db.execute("INSERT INTO windows VALUES(240,NULL)")
    partial = cli(root, "next", "usage")
    assert partial["missing_chats"] == ["lead"] and partial["measured"]["lead_tokens"] == 22


def continuing_role(root):
    root.mkdir()
    init(root, "--slots", 2)
    own = add(root, "catalog", "measure")
    external = add(root, "audit", "independent count")
    for tid, actor in ((own, "catalog-owner"), (external, "auditor")):
        deliver(root, tid, cli(root, "lead", "dispatch", tid), actor)
        cli(root, "lead", "accept", tid, "checked")
    for detail in ([], ["--full-brief"]):
        next_id = add(root, "catalog", "reconcile", "--after", own, external)
        offer = cli(root, "lead", "dispatch", next_id, "--stage", "diagnosis", *detail)
        assert offer["threadId"] == "catalog-owner" and offer["model"] == "capable-local"
        known, other = offer["brief"]["prerequisites"]
        assert bool(known["evidence"]) == bool(detail) and known["review"] == "checked"
        assert other["evidence"] and other["review"] == "checked"
        key = offer["brief"]["ticket"]
        cli(root, "catalog-owner", "claim", next_id, key)
        route = cli(root, "lead", "next-stage", next_id, "--stage", "bounded", "--work-type", "counts")
        assert route["threadId"] == "catalog-owner" and route["model"] == "fast-local"
        cli(root, "catalog-owner", "submit", next_id, key, "checked counts")
        cli(root, "lead", "accept", next_id, "accepted")


def first_contact(root):
    root.mkdir()
    assert cli(root, "first-chat", "intake") == {"stage": "new", "project_notes": ""}
    assert not (root.parent / "codex").exists() and not list(root.iterdir())
    (root / "README").write_text("Goal: issue the quarterly report.\nNext: verify totals against signed source.",
                                 encoding="utf8")
    result = cli(root, "first-chat", "intake")
    assert result["stage"] == "new" and "verify totals" in result["project_notes"]
    assert not (root.parent / "codex").exists()
    (root / "AGENT_STOP").touch()
    assert cli(root, "first-chat", "intake") == {"stage": "stopped"}


def bulk_plan(root):
    root.mkdir()
    path = root / "plan.json"
    card = {"title": "tasks", "scope": "inputs", "role": "A", "criterion": "matches", "hours": 0.25}
    path.write_text(json.dumps([card, dict(card, role="B", after=[12])]), encoding="utf8")
    assert "earlier card" in cli(root, "lead", "init", "--goal", "G", "--simple-model", "S",
                                 "--strong-model", "L", "--cards", path, fail=True)
    assert cli(root, "lead", "intake")["stage"] == "new"
    path.write_text(json.dumps([card, dict(card, role="B"), dict(card, role="C", after=[1, 2])]), encoding="utf8")
    assert init(root, "--cards", path, "--slots", 2)["cards"] == [1, 2, 3]
    assert [c["id"] for c in cli(root, "lead", "plan", "--ready")["tasks"]] == [1, 2]
    for i in (1, 2):
        ready = cli(root, "lead", "dispatch", i)
        assert ready["operation"] == "create_thread" and Path(ready["brief"]["project"]) == root.resolve()
    assert not cli(root, "lead", "plan", "--ready")["tasks"]


if __name__ == "__main__":
    with TemporaryDirectory() as tmp:
        base = Path(tmp)
        first_contact(base / "intake")
        isolated = base / "handoff"
        isolated.mkdir()
        handoff_usage(isolated / "project")
        continuing_role(base / "catalog-project")
        role_project(base / "Sample workspace Ω")
        bulk_plan(base / "bulk")
        for mode in ("tasks", "pipeline"):
            other_modes(base / mode, mode)
    print("OK: roles, tasks, pipeline, atomic dispatch, review, budgets, pause, measured usage")
