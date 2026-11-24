"""Read-only, cursor-based coordinator and roster views of a local team board."""

from base64 import urlsafe_b64encode
from collections import Counter, defaultdict
from contextlib import closing
import hashlib
import json
import math
import re
import sqlite3
import time


def actions(db, project, plan, initiative):
    ready = plan(db, project, True, with_offers=True)
    organized = ready.get("organization", False)
    cards = list(db.execute("""SELECT t.id,role,state,thread FROM tasks t
                               LEFT JOIN chats c ON c.slot=t.slot ORDER BY t.id"""))
    counts = Counter(c["state"] for c in cards)
    if counts.keys() - {"open","reserved","working","review","accepted"}:
        raise ValueError("unknown task state; inspect plan before dispatch")
    workers = [dict(c) for c in cards if c["state"] in ("reserved","working")]
    pulse = initiative["pulse"](db, project, ready)
    if pulse["active"]:
        milestone = pulse["milestone"]
        decision = pulse["owner_decision"]
    else:
        milestone = decision = None
    services = ready.get("service_jobs", [])
    if services:
        threads = dict(db.execute("SELECT slot,thread FROM chats WHERE slot LIKE 'service/%'"))
        for job in services:
            job["thread"] = threads.get("service/" + job["kind"])
    review = ready["review_ready"] if organized else [c["id"] for c in cards if c["state"] == "review"]
    return {"paused": bool(project["paused"]), "slots": project["slots"], "counts": dict(counts),
            "workers": workers, "dispatch": ready["dispatch"], "review_ready": review, "service_jobs": services,
            "milestone": milestone, "owner_decision": decision}


def fingerprint(value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode()).digest()


def coordinator_next(state, revisions, prep):
    if state["owner_decision"]:
        return {"action": "decision", "decision": state["owner_decision"]}
    if state["review_ready"]:
        if revisions is None:
            return {"action": "inspect", "tasks": state["review_ready"]}
        return {"action": "review", "submissions": revisions}
    tests = [{"job": j["id"], "run": j["test"]["run"], "status": j["test"]["status"]}
             for j in state["service_jobs"] if j.get("test") and j["test"]["status"] != "running"]
    if tests:
        return {"action": "review_test", "tests": tests}
    if state["dispatch"]:
        return {"action": "dispatch", "cards": state["dispatch"]}
    if state["workers"] or state["service_jobs"]:
        if prep:
            return {"action": "prepare", "step": prep}
        return {"action": "wait"}
    return {"action": "reconcile"}


def changed_parts(scope, board, card, after):
    old = re.fullmatch(r"p1\.([A-Za-z0-9_-]{22})\.([A-Za-z0-9_-]{22})", after or "")
    previous = old.groups() if old else (None, None)
    tags = ["p1"]
    update = {}
    for (name, data), prior in zip((("board", board), ("card", card)), previous):
        raw = fingerprint([scope, name, data])[:16]
        tag = urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        tags.append(tag)
        if tag != prior:
            update[name] = data
    if update:
        return {"changed": True, "cursor": ".".join(tags), **update}
    return None


def chat_wait(db, scope, organized, settled, now, services=()):
    # A new ticket, binding or stage re-arms even a previously idle permanent chat.
    assignments = defaultdict(list)
    rows = db.execute("""SELECT c.thread,t.id,t.ticket,t.state FROM tasks t JOIN chats c ON c.slot=t.slot
                         WHERE t.state IN ('reserved','working') ORDER BY t.id""")
    for thread, card, ticket, state in rows:
        assignments[thread].append(("card", card, ticket, state))
    if organized:
        rows = db.execute("""SELECT c.thread,j.id,j.ticket,j.state FROM jobs j
                             JOIN chats c ON c.slot='service/'||j.kind
                             WHERE j.state IN ('offered','active') ORDER BY j.id""")
        for thread, job, ticket, state in rows:
            assignments[thread].append(("job", job, ticket, state))
    targets = []
    deadlines = []
    for job in services:
        if (run := job.get("test")) and job.get("thread"):
            assignments[job["thread"]].append(("test",run["run"],run["status"],run["side"]))
            if run["status"]=="running" and run["due"]>now:
                deadlines.append(run["due"])
    for thread, leases in sorted(assignments.items()):
        token = fingerprint([scope, thread, leases]).hex()
        observed = settled.get(token)
        if observed is not None and observed <= now < observed + 30:
            deadlines.append(observed + 30)
        else:
            targets.append({"threadId": thread, "assignment": token})
    return {"targets": targets, "recheck_at": min(deadlines, default=None)}


def observe(path, root, args, actor, plan, initiative, flow, show_card):
    if not math.isfinite(args.wait) or args.wait < 0:
        raise ValueError("use a finite, nonnegative wait")
    if args.roster and args.watch:
        raise ValueError("--roster and --watch cannot be combined; use inbox --roster for owners or inbox --watch for chat waits")
    if args.watch and (args.next or args.reviews):
        raise ValueError("--watch cannot be combined with --next or --reviews")
    if args.prep and not args.next:
        raise ValueError("--prep requires --next")
    if args.settled and not args.watch:
        raise ValueError("--settled requires --watch, which is available in the actions view")
    if args.parts and args.card is None:
        raise ValueError("--parts requires --card ID")
    settled = {}
    received = time.time()
    for assignment, stamp in args.settled or ():
        observed = float(stamp)
        if not math.isfinite(observed):
            raise ValueError("expected finite Unix seconds for --settled")
        if observed <= received < observed + 30:
            settled[assignment] = observed
    deadline = time.monotonic() + args.wait
    previous = None
    recheck_at = None
    baseline = None
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=8)) as db:
        db.row_factory = sqlite3.Row
        while True:
            version = db.execute("PRAGMA data_version").fetchone()[0]
            stopped = (root / "AGENT_STOP").exists()
            now = time.time()
            if previous != (version, stopped) or (recheck_at is not None and now >= recheck_at):
                previous = (version, stopped)
                with db:
                    db.execute("BEGIN")
                    exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='project'").fetchone()
                    p = db.execute("SELECT * FROM project").fetchone() if exists else None
                    if p is None:
                        raise ValueError("no team board for this project")
                    if args.watch and actor != p["lead"]:
                        if not actor:
                            raise ValueError("missing CODEX_THREAD_ID for --watch; forward the caller's chat ID")
                        raise ValueError("only the project lead can plan chat waits; team.py intake identifies the current lead")
                    if args.reviews or args.next:
                        organized = flow["enabled"](db)
                        if args.reviews and not organized:
                            raise ValueError("--reviews requires an organized board with submitted revisions")
                        halted = stopped or bool(p["paused"])
                        if args.reviews:
                            candidates = [] if halted else flow["review_candidates"](db, p)
                            state = {"reviews": candidates, "paused": bool(p["paused"])}
                        elif halted:
                            state = {"action": "stop" if stopped else "paused"}
                        else:
                            board = actions(db, p, plan, initiative)
                            revisions = flow["review_candidates"](db, p) if organized and board["review_ready"] else None
                            state = coordinator_next(board, revisions, args.prep)
                            recheck_at = min((j["test"]["due"] for j in board["service_jobs"]
                                              if j.get("test", {}).get("status") == "running"), default=None)
                    elif args.roster:
                        if not flow["enabled"](db):
                            raise ValueError("no domain roster; use inbox for this board")
                        state = {"roster": flow["roster"](db)}
                    else:
                        state = actions(db, p, plan, initiative)
                        if stopped or p["paused"]:
                            state.update(dispatch=[], review_ready=[])
                            for job in state["service_jobs"]:
                                job.pop("short_ready", None)
                        deadlines = [job["test"]["due"] for job in state["service_jobs"]
                                     if job.get("test",{}).get("status")=="running"]
                        recheck_at = min(deadlines,default=None)
                        if args.watch:
                            watch = {"targets": [], "recheck_at": None} if stopped else chat_wait(
                                db, [str(path), p["created"], actor], flow["enabled"](db), settled, now,state["service_jobs"])
                            state["watch"] = watch
                            recheck_at = watch["recheck_at"]
                    state["stopped"] = stopped
                    scope = [str(path), p["created"], actor]
                    if args.card is not None:
                        card = show_card(db, p, args.card, actor)
                    if args.parts:
                        update = changed_parts([*scope, args.card, args.watch], state, card, args.after)
                    else:
                        if args.card is not None:
                            state["card"] = card
                        cursor = fingerprint([*scope, args.roster, state]).hex()
                        actionable = ((args.reviews and (state["reviews"] or state["paused"] or stopped))
                                      or (args.next and state["action"] != "wait"))
                        if args.after is None and baseline is None and args.wait > 0 and not actionable:
                            # Without a cursor, take the initial snapshot as the
                            # baseline. Return subsequent changes or the baseline
                            # cursor on timeout.
                            baseline = cursor
                            update = None
                        else:
                            update = None if cursor == (args.after or baseline) else {"changed": True, "cursor": cursor, **state}
                if update is not None:
                    if args.fields and not args.parts:
                        wanted = set(args.fields) | {"changed", "cursor", "stopped"}
                        update = {k: v for k, v in update.items() if k in wanted}
                    return update
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                result = {"changed": False}
                if args.after is None and not args.parts and baseline is not None:
                    result["cursor"] = baseline
                return result
            time.sleep(min(remaining, 0.25))
