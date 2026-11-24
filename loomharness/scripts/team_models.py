"""Route working stages using independently scored, project-local paired trials."""

from pathlib import Path
from contextlib import closing
import json
import math
import os
import secrets
import sqlite3
import sys

try:
    import hk
except ImportError:  # python -I excludes the script directory from sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import hk

STAGES = ("bounded", "standard", "diagnosis", "waiting")


def check(value, message):
    if not value:
        raise ValueError(message)


def table(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()


def read(path):
    return json.loads(sys.stdin.read() if str(path) == "-" else path.read_text(encoding="utf-8-sig"))


def fast_model(root):
    path = root / ".codex/harness-models.json"
    if path.is_file():
        config = read(path)
        name = config.get("routine") if isinstance(config, dict) else None
        check(isinstance(name, str) and name.strip(), "harness-models.json needs a routine model name")
        return name.strip()


def arguments(cmds, text):
    stage = cmds.add_parser("next-stage", help="route a checkpoint to the current worker")
    stage.add_argument("id", type=int)
    stage.add_argument("--stage", choices=STAGES, required=True)
    stage.add_argument("--work-type", type=text)
    pack = cmds.add_parser("model-pack", help="prepare blind paired evaluation inputs")
    pack.add_argument("work_type", type=text)
    pack.add_argument("manifest", type=Path)
    score = cmds.add_parser("model-score", help="independent reviewer scores a paired trial")
    score.add_argument("batch", type=int)
    score.add_argument("results", type=Path)
    cmds.add_parser("model-status")


def artifact(root, name):
    check(isinstance(name, str) and name.strip(), "provide a case file")
    path = (root / name).resolve(strict=True)
    check(path.is_file() and path.is_relative_to(root), "use a regular file in this project")
    return hk.load("harness")["cached_digest"](path)


def prepare(db, p, root, a):
    fast = fast_model(root)
    check(
        fast and fast not in (p["simple_model"], p["strong_model"]),
        "configure a distinct routine model for the trial",
    )
    manifest = read(a.manifest)
    check(isinstance(manifest, list) and manifest, "provide a list of historical cases")
    keys, inputs, rows = set(), set(), []
    for spec in manifest:
        check(
            isinstance(spec, dict) and set(spec) == {"job", "task", "input"},
            "a case needs job, task and a blind input file",
        )
        key = (spec["job"], spec["task"])
        check(all(type(i) is int for i in key), "use historical job and card IDs")
        old = db.execute("SELECT revision,decision FROM review_items WHERE job=? AND task=?", key).fetchone()
        check(old and old["decision"] in ("approve", "changes"), "case needs an independent historical verdict")
        digest = artifact(root, spec["input"])
        check(digest not in inputs, "duplicate blind input")
        version = (spec["task"], old["revision"])
        check(version not in keys, "use distinct historical card revisions")
        keys.add(version)
        inputs.add(digest)
        rows.append((secrets.token_hex(8), spec["input"], digest, old["decision"]))
    check({r[3] for r in rows} == {"approve", "changes"}, "include both accepted and returned examples")
    db.execute("""CREATE TABLE IF NOT EXISTS model_trials(id INTEGER PRIMARY KEY, work_type TEXT, base TEXT,
                  fast TEXT, state TEXT NOT NULL, summary TEXT)""")
    db.execute("""CREATE TABLE IF NOT EXISTS model_cases(batch INTEGER REFERENCES model_trials, alias TEXT,
                  path TEXT, digest TEXT, expected TEXT, PRIMARY KEY(batch,alias))""")
    pair = (a.work_type, p["simple_model"], fast)
    fingerprint = {row[1:] for row in rows}
    for (batch,) in db.execute(
        "SELECT id FROM model_trials WHERE work_type=? AND base=? AND fast=? AND state='pending'", pair
    ):
        existing = [
            tuple(row)
            for row in db.execute("SELECT alias,path,digest,expected FROM model_cases WHERE batch=?", (batch,))
        ]
        if {row[1:] for row in existing} == fingerprint:
            rows = existing
            break
    else:
        batch = db.execute(
            "INSERT INTO model_trials(work_type,base,fast,state) VALUES(?,?,?,'pending')", pair
        ).lastrowid
        db.executemany("INSERT INTO model_cases VALUES(?,?,?,?,?)", [(batch, *r) for r in rows])
    return {
        "batch": batch,
        "work_type": a.work_type,
        "models": [p["simple_model"], fast],
        "thinking": "low",
        "cases": [{"case": alias, "input": path, "sha256": digest} for alias, path, digest, _ in sorted(rows)],
    }


def measures(history, result, model, marker, used):
    ids = list(history)
    first, last = result.get("first_turn"), result.get("last_turn")
    check(
        first in ids and last in ids and ids.index(first) <= ids.index(last),
        "missing contiguous evaluation turns",
    )
    chosen = ids[ids.index(first) : ids.index(last) + 1]
    check(
        marker in history[first].get("markers", ()) and history[last].get("outcome") == "task_complete",
        "case marker or completed final turn missing",
    )
    tokens = 0
    for ident in chosen:
        turn = history[ident]
        key = (result["thread"], ident)
        check(
            key not in used
            and turn.get("ended")
            and not turn.get("invalid")
            and turn.get("profiles") == {(model, "low")}
            and turn.get("tokens"),
            "incomplete, reused or wrong-model turn",
        )
        used.add(key)
        tokens += turn["tokens"][0] + turn["tokens"][2]
    start, end = history[first].get("start"), history[last].get("stop")
    check(
        isinstance(start, (int, float))
        and isinstance(end, (int, float))
        and math.isfinite(end - start)
        and end > start
        and tokens > 0,
        "elapsed time and usage required",
    )
    return end - start, tokens


def score(path, root, a, actor):
    def snapshot(db):
        p = db.execute("SELECT * FROM project").fetchone()
        owner = db.execute("SELECT thread FROM chats WHERE slot='service/review'").fetchone()
        check(
            not (root / "AGENT_STOP").exists() and not p["paused"] and owner and owner[0] == actor,
            "active project and independent reviewer required",
        )
        check(table(db, "model_trials"), "no evaluation batches")
        trial = db.execute("SELECT * FROM model_trials WHERE id=?", (a.batch,)).fetchone()
        check(trial and trial["state"] != "revoked", "unknown or revoked evaluation")
        check(
            trial["base"] == p["simple_model"] and trial["fast"] == fast_model(root),
            "model configuration changed",
        )
        return trial

    with closing(sqlite3.connect(path, timeout=8)) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        trial = snapshot(db)
        if trial["summary"]:
            return json.loads(trial["summary"])
        cases = {r["alias"]: r for r in db.execute("SELECT * FROM model_cases WHERE batch=?", (a.batch,))}
    for r in cases.values():
        check(artifact(root, r["path"]) == r["digest"], "blind input changed")
    arms = read(a.results)
    check(
        isinstance(arms, dict) and set(arms) == {trial["base"], trial["fast"]}, "supply both measured model runs"
    )
    usage = hk.load("team_usage")
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").resolve()
    history, used, owners = {}, set(), {}
    metrics = {}
    for model, results in arms.items():
        check(isinstance(results, dict) and results.keys() == cases.keys(), "both models must finish every case")
        seconds, tokens, errors = 0.0, 0, 0
        for alias, r in results.items():
            check(
                isinstance(r, dict) and r.get("input_sha256") == cases[alias]["digest"], "mismatched case input"
            )
            check(artifact(root, r.get("output")) == r.get("output_sha256"), "unverified model output")
            thread = r.get("thread")
            check(
                isinstance(thread, str)
                and thread
                and thread != actor
                and owners.setdefault(thread, model) == model,
                "use separate blind chats for each model, independently graded",
            )
            if thread not in history:
                log = usage["rollout_paths"](home, {thread}).get(thread)
                check(log, "missing evaluation chat log")
                history[thread] = usage["read_turns"](log, thread, trial=True)
            duration, cost = measures(history[thread], r, model, alias + ":" + cases[alias]["digest"], used)
            check(
                type(r.get("quality_pass")) is bool and r.get("decision") in ("approve", "changes"),
                "grade the actual deliverable and record the candidate decision",
            )
            errors += int(not r["quality_pass"] or r["decision"] != cases[alias]["expected"])
            seconds += duration
            tokens += cost
        metrics[model] = {"seconds": seconds, "tokens": tokens, "errors": errors}
    base, fast = metrics[trial["base"]], metrics[trial["fast"]]
    allowed = (
        not (base["errors"] or fast["errors"])
        and fast["seconds"] < base["seconds"]
        and fast["tokens"] <= base["tokens"]
    )
    state = "approved" if allowed else "rejected"
    report = {
        "batch": a.batch,
        "work_type": trial["work_type"],
        "state": state,
        "cases": len(cases),
        "metrics": metrics,
    }
    with closing(sqlite3.connect(path, timeout=8)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN IMMEDIATE")
        latest = snapshot(db)
        if latest["summary"]:
            return json.loads(latest["summary"])
        db.execute("UPDATE model_trials SET state=?,summary=? WHERE id=?", (state, json.dumps(report), a.batch))
    return report


def route(db, p, root, task, tier, work_type, stage, *, checkpoint=False):
    db.execute("""CREATE TABLE IF NOT EXISTS model_routes(task INTEGER PRIMARY KEY REFERENCES tasks,
               work_type TEXT, stage TEXT, model TEXT, trial INTEGER)""")
    previous = db.execute("SELECT work_type,trial FROM model_routes WHERE task=?", (task["id"],)).fetchone()
    kind = work_type or (previous[0] if previous else None)
    stage = stage or {"routine": "bounded", "strong": "diagnosis", "simple": "standard"}[tier]
    check(stage != "waiting", "waiting does not dispatch a worker")
    model, effort, trial = p["simple_model"], "low", None
    if stage == "diagnosis" or task["returns"] >= 2:
        model, effort = p["strong_model"], "medium"
    elif stage == "bounded" and not task["returns"]:
        candidate = fast_model(root)
        row = None
        if candidate and kind and table(db, "model_trials"):
            row = db.execute(
                """SELECT id,state FROM model_trials WHERE work_type=? AND base=? AND fast=?
                              AND state!='pending' ORDER BY id DESC LIMIT 1""",
                (kind, model, candidate),
            ).fetchone()
        if row and row["state"] == "approved":
            model, trial = candidate, row["id"]
    db.execute("INSERT OR REPLACE INTO model_routes VALUES(?,?,?,?,?)", (task["id"], kind, stage, model, trial))
    db.execute("""CREATE TABLE IF NOT EXISTS trial_exposure(task INTEGER REFERENCES tasks,trial INTEGER,
                  PRIMARY KEY(task,trial))""")
    if not checkpoint:
        db.execute("DELETE FROM trial_exposure WHERE task=?", (task["id"],))
    if trial:
        db.execute("INSERT OR IGNORE INTO trial_exposure VALUES(?,?)", (task["id"], trial))
    return model, effort


def returned(db, task):
    if table(db, "model_trials") and table(db, "trial_exposure"):
        db.execute(
            """UPDATE model_trials SET state='revoked' WHERE (work_type,base,fast) IN
            (SELECT m.work_type,m.base,m.fast FROM model_trials m JOIN trial_exposure e
             ON e.trial=m.id WHERE e.task=?)""",
            (task,),
        )


def status(db):
    if not table(db, "model_trials"):
        return []
    return [dict(r) for r in db.execute("SELECT id,work_type,base,fast,state FROM model_trials ORDER BY id DESC")]
