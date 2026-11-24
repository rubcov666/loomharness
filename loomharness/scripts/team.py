"""Local project board for role chats in Codex Desktop."""

import argparse
from contextlib import closing, redirect_stderr, redirect_stdout
import io
import json
import math
import os
from pathlib import Path
import secrets
import shutil
import socketserver
import sqlite3
import sys
import tempfile
import threading
import time

try:
    import hk
except ImportError:  # python -I excludes the script directory from sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import hk


harness_ns = hk.load("harness")
board_path = harness_ns["team_board"]
flow = hk.load("team_flow")
models, queue = flow["models"], flow["queue"]
initiative = hk.load("initiative")
ON_BLOCKER = ("Continue available work. At turn end, tell the coordinator what blocks you, what you checked, "
              "and which answer you need. The coordinator asks the owner.")
# Headless card drivers are implemented in team_driver.py. Desktop dispatch
# uses Codex create_thread/send_message_to_thread operations.
DRIVERS = ("claude", "gemini", "antigravity", "opencode", "dsh")
DRIVER_BINARY = {"claude": "claude", "gemini": "gemini",
                 "antigravity": "antigravity", "opencode": "opencode", "dsh": "dsh"}


def current_driver(db):
    db.execute("CREATE TABLE IF NOT EXISTS driver(id INTEGER PRIMARY KEY CHECK(id=1), name TEXT NOT NULL)")
    row = db.execute("SELECT name FROM driver WHERE id=1").fetchone()
    return row[0] if row else None
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS project (
       id INTEGER PRIMARY KEY CHECK(id=1), goal TEXT, lead TEXT, mode TEXT, slots INTEGER,
       hours_per_role REAL, simple_model TEXT, strong_model TEXT, created REAL, paused INTEGER DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS chats (slot TEXT PRIMARY KEY, thread TEXT UNIQUE NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS windows (start REAL PRIMARY KEY, stop REAL)""",
    """CREATE TABLE IF NOT EXISTS tasks (
       id INTEGER PRIMARY KEY, role TEXT NOT NULL, title TEXT NOT NULL, scope TEXT NOT NULL,
       criterion TEXT NOT NULL, hours REAL NOT NULL, tier TEXT NOT NULL, state TEXT DEFAULT 'open',
       slot TEXT, ticket TEXT, evidence TEXT, review TEXT, returns INTEGER DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS deps (task INTEGER REFERENCES tasks(id), needs INTEGER REFERENCES tasks(id),
       PRIMARY KEY(task, needs))""",
)


def require(ok, message):
    if not ok:
        raise ValueError(message)


def nonempty(value):
    value = value.strip()
    if not value:
        raise argparse.ArgumentTypeError("Expected nonempty text")
    return value


def hours(text):
    value = float(text)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("Expected positive finite hours")
    return value


def arguments():
    return build_parser().parse_args()


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd(), help="original project directory")
    cmds = parser.add_subparsers(dest="cmd", required=True)
    storage = cmds.add_parser("storage", help="show the board used by hooks and CLI")
    storage.add_argument("--workspace", action="store_true", help="place a new board in the project, preserving existing boards")
    serve = cmds.add_parser("serve", help="long-running local board process for fast commands")
    serve.add_argument("--daemon", action="store_true", help="do not stop when stdin closes")
    rpc = cmds.add_parser("rpc", help="run one command through a local serve process")
    rpc.add_argument("--project", type=Path, default=argparse.SUPPRESS, help="project directory for this request")
    rpc.add_argument("--actor", help="CODEX_THREAD_ID for this request")
    rpc.add_argument("argv", nargs=argparse.REMAINDER, help="command and its arguments")
    inbox = cmds.add_parser("inbox", help="read coordinator actions or wait for a changed snapshot")
    view = inbox.add_mutually_exclusive_group()
    view.add_argument("--roster", action="store_true", help="watch domain ownership instead of actions")
    view.add_argument("--card", type=int, metavar="ID", help="include the full card in the same snapshot")
    view.add_argument("--reviews", action="store_true", help="watch only actionable submitted revisions")
    view.add_argument("--next", action="store_true", help="show the coordinator's next action")
    inbox.add_argument("--prep", type=nonempty, metavar="STEP",
                       help="with --next, offer one independent interruptible step from the current plan")
    inbox.add_argument("--parts", action="store_true", help="send only changed board/card sections with --card")
    inbox.add_argument("--watch", action="store_true", help="include bound chats with outstanding assignments")
    inbox.add_argument("--settled", action="append", nargs=2, metavar=("ASSIGNMENT","OBSERVED_AT"),
                       help="a completed idle turn and its original Unix timestamp in seconds")
    inbox.add_argument("--after", help="cursor from this chat's previous snapshot")
    inbox.add_argument("--wait", type=float, default=0, metavar="SECONDS")
    inbox.add_argument("--fields", nargs="+", metavar="KEY",
                       help="only these top-level keys of a changed snapshot")
    cmds.add_parser("intake", help="inspect project context on first use in a chat; read-only")
    init = cmds.add_parser("init")
    flow["git_options"](init)
    flow["add_commands"](cmds, nonempty)
    models["arguments"](cmds, nonempty)
    queue["arguments"](cmds, nonempty, hours)
    initiative["arguments"](cmds, nonempty, hours)
    for key in ("goal", "simple-model", "strong-model"):
        init.add_argument("--" + key, required=True, type=nonempty)
    init.add_argument("--mode", choices=("roles", "tasks", "pipeline"), default="roles")
    init.add_argument("--slots", type=int, default=1)
    init.add_argument("--hours-per-role", type=hours)
    init.add_argument("--cards", metavar="JSON_FILE", help="initial plan as a JSON array; use - for stdin")
    for action in ("add", "edit"):
        sub = cmds.add_parser(action)
        if action == "edit":
            sub.add_argument("id", type=int)
        else:
            sub.add_argument("--authority", type=nonempty, help="user request or prior approved scope")
        for key in ("role", "title", "scope", "criterion"):
            sub.add_argument("--" + key, required=action == "add", type=nonempty)
        sub.add_argument("--hours", required=action == "add", type=hours)
        sub.add_argument("--tier", choices=("routine", "simple", "strong"))
        sub.add_argument("--after", type=int, nargs="*")
        sub.add_argument("--resource", action="append", type=nonempty, help="shared write scope; repeat for multiple")
    cmds.add_parser("plan").add_argument("--ready", action="store_true", help="only dispatchable cards")
    usage = cmds.add_parser("usage")
    usage.add_argument("--summary", action="store_true", help="return totals and coverage without per-participant or role details")
    usage.add_argument("--thread", action="append", metavar="ID", help="measure this chat and its local subagents")
    usage.add_argument("--since", metavar="ISO", help="inclusive timestamp with timezone for chat usage")
    usage.add_argument("--until", metavar="ISO", help="exclusive timestamp with timezone for chat usage")
    cmds.add_parser("pause")
    cmds.add_parser("resume")
    handoff = cmds.add_parser("handoff", help="transfer coordination from this lead to a named chat")
    handoff.add_argument("thread", type=nonempty, help="successor's own thread ID")
    driver = cmds.add_parser("driver", help="Choose a headless driver for card dispatch.")
    driver.add_argument("action", choices=("show", "set"))
    driver.add_argument("name", nargs="?", choices=(*DRIVERS, "desktop"),
                        help="CLI driver from team_driver.py, or desktop for Codex app dispatch.")
    for cmd in ("show", "dispatch", "bind", "claim", "submit", "accept", "revise", "release"):
        sub = cmds.add_parser(cmd)
        sub.add_argument("id", type=int)
        if cmd == "dispatch":
            sub.add_argument("--tier", choices=("routine", "simple", "strong"))
            sub.add_argument("--reply-language", type=nonempty, help="user's output language for this assignment")
            sub.add_argument("--work-type", type=nonempty)
            sub.add_argument("--stage", choices=("bounded","standard","diagnosis"))
            detail = sub.add_mutually_exclusive_group()
            detail.add_argument("--brief", action="store_true", help="include only prerequisite IDs and states")
            detail.add_argument("--full-brief", action="store_true", help="include even this worker's own earlier evidence")
        if cmd == "show":
            sub.add_argument("--brief", action="store_true",
                             help="only id, role, title, state, criterion, revision and thread")
        if cmd in ("claim", "bind", "submit"):
            sub.add_argument("ticket", type=nonempty)
        if cmd == "bind":
            sub.add_argument("thread", type=nonempty)
        if cmd in ("submit", "accept", "revise", "release"):
            sub.add_argument("note", type=nonempty)
        if cmd == "submit":
            sub.add_argument("--quick-review", action="store_true", help="short read-only check on independent inputs")
            sub.add_argument("--revision", type=flow["revision"])
            sub.add_argument("--artifact", type=Path, help="immutable snapshot file, for SHA-256 submissions")
    return parser


def ready_dependencies(db, task, *, brief=False, recipient=None):
    if brief:
        return [dict(r) for r in db.execute(
            "SELECT id,state FROM deps JOIN tasks ON needs=id WHERE task=? ORDER BY id", (task,))]
    # A continuing worker wrote its own evidence. Always include the reviewer's feedback.
    return [dict(r) for r in db.execute("""
        SELECT t.id,t.state,CASE WHEN c.thread=? THEN NULL ELSE t.evidence END AS evidence,t.review
        FROM deps d JOIN tasks t ON t.id=d.needs LEFT JOIN chats c ON c.slot=t.slot
        WHERE d.task=? ORDER BY t.id""", (recipient, task))]


def capacity(p, card, busy, scans):
    if any(c["role"] == card["role"] for c in busy):
        return "role already working"
    if card["id"] in scans:
        return ""
    if sum(c["id"] not in scans for c in busy) >= p["slots"]:
        return "all worker slots occupied"
    return ""


def plan(db, p, ready_only, *, with_offers=False):
    organized = flow["enabled"](db)
    # A card without an explicit resource claims '*'. Expose this to help
    # the coordinator identify unnecessarily broad write scopes.
    exclusive = (", EXISTS(SELECT 1 FROM resources WHERE task=tasks.id AND name='*') AS exclusive"
                 if organized else "")
    cards = [dict(r) for r in db.execute(
        f"SELECT id,role,title,state,hours,returns{exclusive} FROM tasks ORDER BY id")]
    edges = {}
    for task, needs in db.execute("SELECT task,needs FROM deps ORDER BY task,needs"):
        edges.setdefault(task, []).append(needs)
    result = {"goal": p["goal"], "mode": p["mode"], "paused": bool(p["paused"]), "slots": p["slots"],
              "hours_per_role": p["hours_per_role"]}
    if ready_only:
        busy = [c for c in cards if c["state"] in ("reserved", "working")]
        scans = initiative["scan_ids"](db)
        accepted = {c["id"] for c in cards if c["state"] == "accepted"}
        cards = [c for c in cards if not p["paused"] and c["state"] == "open"
                 and accepted.issuperset(edges.get(c["id"], [])) and not capacity(p, c, busy, scans)]
        resources = {} if organized else None
        if organized and any(c["id"] not in scans for c in cards):
            for task, name in db.execute("""SELECT r.task,r.name FROM tasks t JOIN resources r ON r.task=t.id
                                            WHERE t.state IN ('open','reserved','working')"""):
                resources.setdefault(task, set()).add(name)
            occupied = set()
            for c in busy:
                if c["id"] not in scans:
                    occupied.update(resources.get(c["id"], ()))
            cards = [c for c in cards if c["id"] in scans or not initiative["overlaps"](
                occupied, resources.get(c["id"], ()))]
        if with_offers:
            result["dispatch"] = initiative["offers"](p, cards, busy, scans, resources)
    if organized:
        result["organization"] = True
        result["review_ready"] = flow["ready_reviews"](db, p)
        result["service_jobs"] = [dict(r) for r in db.execute(
            "SELECT id,kind,state FROM jobs WHERE state IN ('offered','active') ORDER BY id")]
        for job in result["service_jobs"]:
            if job["kind"] == "review" and (run := queue["current"](db,job["id"])):
                job["test"] = run
                if run["status"] == "running" and time.time() >= run["due"]:
                    run["status"] = "check"
                if not p["paused"] and (available := queue["candidates"](db,run)):
                    job["short_ready"] = available
    return dict(result, tasks=[dict(c, after=edges.get(c["id"], [])) for c in cards])


def initialize(db, a, actor):
    require(a.slots > 0, "slots must be positive")
    for statement in SCHEMA:
        db.execute(statement)
    require(not db.execute("SELECT 1 FROM project").fetchone(), "board already initialized; use plan")
    now = time.time()
    db.execute("""INSERT INTO project(id,goal,lead,mode,slots,hours_per_role,simple_model,strong_model,created)
                  VALUES (1,?,?,?,?,?,?,?,?)""",
               (a.goal, actor, a.mode, a.slots, a.hours_per_role, a.simple_model, a.strong_model, now))
    db.execute("INSERT INTO windows VALUES(?,NULL)", (now,))
    ids = []
    if a.cards:
        raw = sys.stdin.read() if a.cards == "-" else Path(a.cards).read_text(encoding="utf-8-sig")
        cards = json.loads(raw)
        require(isinstance(cards, list), "cards must be an array")
        project = db.execute("SELECT * FROM project").fetchone()
        for card in cards:
            required = {"role", "title", "scope", "criterion"}
            require(isinstance(card, dict) and set(card) <= required | {"hours", "tier", "after"} and
                    all(isinstance(card.get(k), str) and card[k].strip() for k in required),
                    "every card needs role, title, scope, criterion and hours")
            value = card.get("hours")
            require(type(value) in (int, float) and math.isfinite(value) and value > 0, "invalid card hours")
            deps = card.get("after", [])
            tier = card.get("tier", "simple")
            require(isinstance(deps, list) and all(type(d) is int for d in deps) and tier in ("routine", "simple", "strong"),
                    "invalid after or tier")
            fields = dict(card, after=deps, tier=tier)
            ids.append(save_card(db, project, argparse.Namespace(**fields))["id"])
    return {"lead": actor, "mode": a.mode, "cards": ids}


def save_card(db, p, a):
    task = getattr(a, "id", None)
    old = {"tier": "simple"}
    if task is not None:
        row = db.execute("SELECT * FROM tasks WHERE id=? AND state='open'", (task,)).fetchone()
        require(row, "only open cards can be edited")
        old = dict(row)
        require(not old["slot"] or a.role in (None, old["role"]), "keep the owner of a dispatched card")
    card = {key: getattr(a, key) or old[key] for key in ("role", "title", "scope", "criterion", "hours", "tier")}
    organized = flow["enabled"](db)
    keys = getattr(a, "resource", None)
    if organized:
        require(db.execute("SELECT 1 FROM domains WHERE name=?", (card["role"],)).fetchone(), "unknown domain; see roster")
    else:
        require(not keys, "enable organize before reserving resources")
    spent = db.execute("SELECT coalesce(sum(hours),0) FROM tasks WHERE role=? AND id IS NOT ?",
                       (card["role"], task)).fetchone()[0]
    require(p["hours_per_role"] is None or spent + card["hours"] <= p["hours_per_role"] + 1e-9,
            "role exceeds the agreed planned hours")
    if task is None:
        task = db.execute("""INSERT INTO tasks(role,title,scope,criterion,hours,tier)
                             VALUES(:role,:title,:scope,:criterion,:hours,:tier)""", card).lastrowid
    else:
        db.execute("""UPDATE tasks SET role=:role,title=:title,scope=:scope,criterion=:criterion,
                      hours=:hours,tier=:tier WHERE id=:id""", dict(card, id=task))
    if organized and (keys or not old.get("id")):
        db.execute("DELETE FROM resources WHERE task=?", (task,))
        db.executemany("INSERT INTO resources VALUES(?,?)", [(task, k) for k in {v.casefold() for v in keys or ["*"]}])
    if a.after is not None:
        db.execute("DELETE FROM deps WHERE task=?", (task,))
        for dep in sorted(set(a.after)):
            require(dep < task and db.execute("SELECT 1 FROM tasks WHERE id=?", (dep,)).fetchone(),
                    f"prerequisite must be an existing earlier card: {dep}")
            db.execute("INSERT INTO deps VALUES(?,?)", (task, dep))
    if p["mode"] == "pipeline":
        same = db.execute("""SELECT 1 FROM deps JOIN tasks child ON child.id=task JOIN tasks parent ON parent.id=needs
                             WHERE (child.id=? OR parent.id=?) AND child.role=parent.role""", (task, task)).fetchone()
        require(not same, "successive pipeline stages need different owners")
    return {"id": task}


def dispatch(db, p, t, a, root):
    require(t["state"] == "open", "already dispatched or closed; inspect show before retry")
    slot = f'task/{a.id}' if p["mode"] == "tasks" else f'role/{t["role"]}'
    owner = db.execute("SELECT thread FROM chats WHERE slot=?", (slot,)).fetchone()
    drv = current_driver(db)
    recipient = owner[0] if owner and not drv and not a.full_brief else None
    dependencies = ready_dependencies(db, a.id, brief=a.brief, recipient=recipient)
    require(all(d["state"] == "accepted" for d in dependencies), "prerequisite awaits acceptance")
    active = db.execute("SELECT id,role FROM tasks WHERE state IN ('reserved','working')").fetchall()
    scans = initiative["scan_ids"](db)
    organized = flow["enabled"](db)
    reason = capacity(p, t, active, scans)
    require(not reason, reason)
    if organized and a.id not in scans:
        require(not any(r[0] not in scans for r in flow["conflicts"](db, a.id)), "shared write scope occupied")
    ticket = secrets.token_hex(10)
    tier = a.tier or t["tier"]
    model, thinking = models["route"](db,p,root,t,tier,a.work_type,a.stage)
    db.execute("UPDATE tasks SET state='reserved',slot=?,ticket=?,tier=? WHERE id=?", (slot, ticket, tier, a.id))
    if organized and t["slot"] is None:
        flow["ensure_review_items"](db)
        db.execute("INSERT INTO card_timings(task,dispatched) VALUES(?,?)", (a.id,time.time()))
    result = {"operation": "send_message_to_thread" if owner else "create_thread",
            "threadId": owner[0] if owner else None, "model": model,
            "thinking": thinking,
            "brief": {"project": str(root), "id": a.id, "ticket": ticket, "goal": p["goal"], "role": t["role"],
                      "task": t["title"], "scope": t["scope"], "acceptance": t["criterion"], "hours": t["hours"],
                      "prerequisites": dependencies, "feedback": t["review"]}}
    if a.reply_language:
        result["brief"]["reply_language"] = a.reply_language
    if a.id in scans:
        result["brief"]["analysis_only"] = True
        result["brief"]["instruction"] = ("Read stable sources and write only your report. Give the coordinator "
            "the user impact, remaining gap, and options. The owner decides on project changes.")
    if drv:
        binary = DRIVER_BINARY.get(drv, drv)
        require(shutil.which(binary), f"CLI '{binary}' not found on PATH. Install it or restore desktop mode: "
                                      f"team.py driver set claude|gemini|antigravity|opencode|dsh")
        helper = Path(__file__).resolve().with_name("team_driver.py")
        result = {"operation": "run_command",
                  "command": [Path(sys.executable).resolve().as_posix(), "-I", "-X", "utf8", helper.as_posix(),
                              "run", "--cli", drv, "--model", model, "--project", str(root),
                              "--task", str(a.id), "--ticket", ticket],
                  "thinking": thinking, "brief": result["brief"]}
        if a.reply_language:
            result["command"].extend(["--reply-language", a.reply_language])
    return result


def show_card(db, project, task_id, actor, brief=False):
    require(actor, "missing CODEX_THREAD_ID")
    task = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    require(task, "unknown task")
    card = dict(task)
    owner = db.execute("SELECT thread FROM chats WHERE slot=?", (task["slot"],)).fetchone()
    thread = owner[0] if owner else None
    organized = flow["enabled"](db)
    if brief:
        keep = {"id", "role", "title", "state", "criterion", "hours", "slot"}
        card = {k: card[k] for k in keep}
        if actor != project["lead"]:
            card.pop("ticket", None)
        if organized:
            row = db.execute("SELECT revision FROM submissions WHERE task=?", (task_id,)).fetchone()
            card["revision"] = row[0] if row else None
        return dict(card, thread=thread)
    if organized:
        row = db.execute("SELECT revision FROM submissions WHERE task=?", (task_id,)).fetchone()
        card.update(revision=row[0] if row else None, resources=[r[0] for r in db.execute(
            "SELECT name FROM resources WHERE task=? ORDER BY name", (task_id,))])
    if actor != project["lead"]:
        card.pop("ticket")
    return dict(card, thread=thread, prerequisites=ready_dependencies(db, task_id))


def operate(db, root, a, actor):
    if a.cmd == "init":
        require(actor, "missing CODEX_THREAD_ID")
        result = initialize(db, a, actor)
        flow["save_git"](db, root, a)
        return result
    p = db.execute("SELECT * FROM project").fetchone()
    if a.cmd == "plan":
        return plan(db, p, a.ready)
    if a.cmd == "show":
        return show_card(db, p, a.id, actor, brief=getattr(a, "brief", False))
    if a.cmd == "pulse":
        if not initiative["current"](db):
            return {"active": False}
        return initiative["pulse"](db, p, plan(db, p, True, with_offers=True))
    require(actor or a.cmd in ("roster", "job"), "missing CODEX_THREAD_ID")
    lead = actor == p["lead"]
    if a.cmd == "handoff":
        # The surrounding IMMEDIATE transaction serializes transfers, even for pre-handoff boards.
        db.execute("""CREATE TABLE IF NOT EXISTS lead_handoffs (
            id INTEGER PRIMARY KEY, previous TEXT NOT NULL, successor TEXT NOT NULL, at REAL NOT NULL)""")
        if not lead:
            last = db.execute("SELECT previous,successor FROM lead_handoffs ORDER BY id DESC LIMIT 1").fetchone()
            require(last and tuple(last) == (actor, a.thread) and p["lead"] == a.thread,
                    "only the current project lead can transfer coordination")
        else:
            require(a.thread != actor, "choose a different chat for handoff")
            require(not db.execute("SELECT 1 FROM chats WHERE thread=?", (a.thread,)).fetchone(),
                    "successor already owns a worker or service role")
            db.execute("INSERT INTO lead_handoffs(previous,successor,at) VALUES(?,?,?)", (actor, a.thread, time.time()))
            db.execute("UPDATE project SET lead=? WHERE id=1", (a.thread,))
        return {"previousLeadThreadId": actor, "leadThreadId": a.thread}
    services = {"review", "delivery", "job", "job-bind", "job-claim", "job-cancel", "verdict", "verdicts", "delivered", "roster",
                "review-side", "review-interrupt"}
    if a.cmd == "model-status":
        require(lead,"coordinator required")
        return models["status"](db)
    if a.cmd not in {"claim", "submit"} | services:
        require(lead, "only the project lead can do this")
    if a.cmd in ("pause", "resume"):
        paused = a.cmd == "pause"
        if bool(p["paused"]) != paused:
            if paused:
                db.execute("UPDATE windows SET stop=? WHERE stop IS NULL", (time.time(),))
            else:
                db.execute("INSERT INTO windows VALUES(?,NULL)", (time.time(),))
            db.execute("UPDATE project SET paused=?", (int(paused),))
        return a.cmd
    if a.cmd not in ("job", "roster"):
        require(not p["paused"], "project paused")
    if a.cmd == "organize":
        return flow["organize"](db, p, root, a)
    organized = flow["enabled"](db)
    if a.cmd == "model-pack":
        require(organized and flow["has_review_items"](db),"historical independent review cases required")
        return models["prepare"](db,p,root,a)
    if a.cmd in initiative["COMMANDS"]:
        require(lead and organized, "only the coordinator of an organized team can manage the survey")
        if a.cmd == "survey":
            return initiative["start"](db, p, a, save_card)
        survey = initiative["current"](db)
        require(survey, "start a milestone survey first")
        if a.cmd == "proposal":
            return initiative["propose"](db, survey, a)
        if a.cmd == "decide":
            return initiative["decide"](db, p, survey, a, save_card)
        return initiative["close"](db, survey, a.evidence)
    if a.cmd == "git-choice":
        require(not organized or not db.execute("SELECT 1 FROM jobs WHERE kind='delivery' AND state IN ('active','offered')").fetchone(),
                "finish or reconcile the active delivery before changing Git policy")
        return flow["save_git"](db, root, a)
    if a.cmd == "driver":
        db.execute("CREATE TABLE IF NOT EXISTS driver(id INTEGER PRIMARY KEY CHECK(id=1), name TEXT NOT NULL)")
        if a.action == "set":
            require(a.name, "Choose a driver: claude, gemini, antigravity, opencode, dsh or desktop.")
            if a.name == "desktop":
                db.execute("DELETE FROM driver")
            else:
                db.execute("INSERT OR REPLACE INTO driver VALUES(1,?)", (a.name,))
            return {"driver": a.name}
        return {"driver": current_driver(db) or "desktop"}
    if a.cmd in services:
        require(organized, "enable organize first")
        return flow["service"](db, p, root, a, actor)
    if organized and a.cmd in ("accept", "revise"):
        raise ValueError("independent reviewer must issue verdict")
    if a.cmd in ("add", "edit"):
        if initiative["current"](db):
            if a.cmd == "add":
                require(a.authority, "record the user's request or use proposal and decide for new scope")
                card = save_card(db, p, a)
                db.execute("INSERT INTO direct_work VALUES(?,?)", (card["id"],a.authority))
                return card
            if a.id in initiative["scan_ids"](db):
                require(not any((a.title,a.role,a.scope,a.criterion,a.resource)), "survey assignments are analysis-only")
            if db.execute("SELECT 1 FROM approved_work WHERE task=?", (a.id,)).fetchone():
                require(not any((a.title,a.role,a.scope,a.criterion)), "preserve the scope of the selected option")
        return save_card(db, p, a)
    task = db.execute("SELECT * FROM tasks WHERE id=?", (a.id,)).fetchone()
    require(task, "unknown task")
    owner = db.execute("SELECT thread FROM chats WHERE slot=?", (task["slot"],)).fetchone()
    if a.cmd == "dispatch":
        return dispatch(db, p, task, a, root)
    if a.cmd == "next-stage":
        require(task["state"]=="working" and owner,"working card with bound owner required")
        if a.stage=="waiting":
            return {"operation":"wait","threadId":owner[0]}
        model,thinking=models["route"](db,p,root,task,task["tier"],a.work_type,a.stage,checkpoint=True)
        return {"operation":"send_message_to_thread","threadId":owner[0],"model":model,
                "thinking":thinking,"task":a.id,"stage":a.stage}
    if a.cmd == "bind":
        require(task["slot"] and task["ticket"] == a.ticket and task["state"] in
                ("reserved", "working", "review", "accepted"), "invalid dispatch ticket")
        require(a.thread != p["lead"] and (not owner or owner[0] == a.thread), "different chat already bound")
        if not owner:
            db.execute("INSERT INTO chats VALUES(?,?)", (task["slot"], a.thread))
    elif a.cmd == "claim":
        require(not lead and task["state"] == "reserved" and task["ticket"] == a.ticket, "invalid assignment")
        require(not owner or owner[0] == actor, "assigned to another chat")
        if not owner:
            db.execute("INSERT INTO chats VALUES(?,?)", (task["slot"], actor))
        db.execute("UPDATE tasks SET state='working' WHERE id=?", (a.id,))
    elif a.cmd == "submit":
        require(owner and owner[0] == actor and task["state"] == "working" and task["ticket"] == a.ticket,
                "only the assigned worker can submit for this dispatch")
        require(organized or not a.quick_review,"short reviews require an organized team")
        if organized:
            require(a.revision, "submit an immutable candidate with --revision")
            artifact = flow["verify_candidate"](db, root, a.revision, a.artifact)
            db.execute("INSERT OR REPLACE INTO submissions VALUES(?,?,?)", (a.id, a.revision, artifact))
            queue["mark_short"](db,a.id,a.revision,a.quick_review)
        db.execute("UPDATE tasks SET state='review',evidence=? WHERE id=?", (a.note, a.id))
    elif a.cmd == "release":
        require(task["state"] in ("reserved", "working"), "no outstanding assignment")
        db.execute("UPDATE tasks SET state='open',ticket=NULL,review=? WHERE id=?", (a.note, a.id))
    else:
        require(task["state"] == "review" and task["evidence"], "worker evidence required before review")
        state = "accepted" if a.cmd == "accept" else "open"
        db.execute("UPDATE tasks SET state=?,review=?,returns=returns+? WHERE id=?",
                   (state, a.note, int(a.cmd == "revise"), a.id))
    return {"id": a.id, "state": db.execute("SELECT state FROM tasks WHERE id=?", (a.id,)).fetchone()[0]}


def intake(root, path, actor):
    """Discover an existing team before reading project notes or asking the owner."""
    if (root / "AGENT_STOP").exists():
        return {"stage": "stopped"}
    policy = None
    if path.is_file():
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=8)) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN")
            policy = flow["git_choice"](db)
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='project'").fetchone()
            project = db.execute("SELECT lead,goal,mode,paused FROM project").fetchone() if exists else None
            if project:
                chat = db.execute("SELECT slot FROM chats WHERE thread=?", (actor,)).fetchone()
                result = {"stage": "existing", "mode": project["mode"], "goal": project["goal"],
                          "leadThreadId": project["lead"], "paused": bool(project["paused"])}
                result["git"] = policy
                if flow["enabled"](db):
                    result["organization"] = True
                if initiative["current"](db):
                    result["survey"] = True
                if actor == project["lead"]:
                    return dict(result, actor="lead")
                if chat:
                    kind = chat["slot"].removeprefix("service/") if chat["slot"] in flow["STAGES"].values() else "worker"
                    return dict(result, actor=kind, slot=chat["slot"])
                if not actor:
                    return dict(result, actor="missing", next="forward the caller's CODEX_THREAD_ID")
                return dict(result, actor="other", threadId=actor, next="for coordination, ask the current lead to run handoff")
    read = hk.load("plan")
    docs = (*read["DEFAULT_DOCS"], "BRIEF.md", "README.md", "README.txt", "README", "Описание.md")
    notes = read["for_prompt"](root, "what is next", default_docs=docs, budget=2000)
    return {"stage": "new", "project_notes": notes, **({"git": policy} if policy else {})}


def run(a, actor, *, stdout=None, stderr=None):
    root = a.project.resolve(strict=True)
    require(root.is_dir(), "project must be a directory")
    if a.cmd == "storage":
        require(not a.workspace or not (root / "AGENT_STOP").exists(), "AGENT_STOP")
        print(json.dumps(harness_ns["board_storage"](root, a.workspace), ensure_ascii=False), file=stdout)
        return
    path = board_path(root)
    if a.cmd == "intake":
        print(json.dumps(intake(root, path, actor), ensure_ascii=False), file=stdout)
        return
    if a.cmd == "inbox":
        require(path.is_file(), "no team board for this project")
        observe = hk.load("inbox")["observe"]
        result = observe(path, root, a, actor, plan, initiative, flow, show_card)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")), file=stdout)
        return
    if a.cmd == "usage":
        usage = hk.load("team_usage")
        if a.thread or a.since or a.until:
            require(a.thread and a.since and a.until, "use --thread ID --since ISO --until ISO together")
            result = usage["window_usage"](a.thread, a.since, a.until)
            if a.summary:
                result["participant_count"] = len(result.pop("participants"))
        else:
            require(path.is_file(), "no team board for this project")
            result = usage["measure"](path)
            if a.summary:
                result["role_count"] = len(result.pop("by_role"))
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")), file=stdout)
        return
    if a.cmd == "review-test":
        require(path.is_file(),"no team board for this project")
        sys.exit(queue["run_test"](path,root,a,actor,flow["verify_candidate"], stdout=stdout, stderr=stderr))
    if a.cmd == "model-score":
        require(path.is_file(),"no team board for this project")
        print(json.dumps(models["score"](path,root,a,actor),ensure_ascii=False), file=stdout)
        return
    reading = a.cmd in ("plan", "show", "roster", "job", "pulse","model-status")
    require(reading or not (root / "AGENT_STOP").exists(), "AGENT_STOP")
    if a.cmd == "git-choice" and not a.git:
        require(a.git_dir is None, "--git-dir needs --git existing")
        policy = None
        if path.is_file():
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=8)) as db:
                db.row_factory = sqlite3.Row
                policy = flow["git_choice"](db)
        print(json.dumps(policy, ensure_ascii=False), file=stdout)
        return
    if a.cmd == "init":
        require(actor and a.slots > 0, "init needs a Codex chat and positive slots")
        path.parent.mkdir(parents=True, exist_ok=True)
    elif a.cmd == "git-choice":
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        require(path.is_file(), "no team board for this project")
    with closing(sqlite3.connect(path, timeout=8)) as db, db:
        db.row_factory = sqlite3.Row
        # WAL lets readers (inbox watch, usage) keep working while a writer
        # holds a transaction, instead of failing on BUSY and retrying turns.
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN" if reading else "BEGIN IMMEDIATE")
        has_project = db.execute("SELECT 1 FROM sqlite_master WHERE name='project'").fetchone()
        if has_project or a.cmd == "init":
            result = operate(db, root, a, actor)
        else:
            require(a.cmd == "git-choice", "no team board for this project")
            result = flow["save_git"](db, root, a)
    # A coordinator can shorten the brief. Also deliver the handoff rule to the actual assignee.
    if a.cmd in ("dispatch", "review", "delivery"):
        result["brief"]["on_blocker"] = ON_BLOCKER
    elif a.cmd in ("claim", "job-claim"):
        result["on_blocker"] = ON_BLOCKER
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")), file=stdout)


# argparse writes help/errors to process-wide streams. The actual calls use request-local streams and actors.
_SERVE_LOCK = threading.Lock()


def execute_request(request, parser):
    argv = list(request.get("argv") or [])
    if argv[:1] == ["--"]:
        argv = argv[1:]
    if project := request.get("project"):
        argv = ["--project", str(project), *argv]
    actor = request.get("actor")
    output = io.StringIO()
    try:
        with _SERVE_LOCK:
            with redirect_stdout(output), redirect_stderr(output):
                parsed = parser.parse_args(argv)
            if parsed.cmd in ("serve", "rpc"):
                raise ValueError("serve/rpc are not available inside a serve request")
            if parsed.cmd not in ("inbox", "review-test"):
                run(parsed, actor, stdout=output, stderr=output)
                return {"ok": True, "stdout": output.getvalue()}
        # A writer must be able to wake a waiting inbox or act while a background test runs.
        run(parsed, actor, stdout=output, stderr=output)
    except SystemExit as exc:
        if exc.code not in (0, None):
            return {"ok": False, "stdout": output.getvalue(), "error": output.getvalue().strip() or f"exit {exc.code}"}
    except (ValueError, OSError, sqlite3.Error) as exc:
        return {"ok": False, "stdout": output.getvalue(), "error": str(exc)}
    return {"ok": True, "stdout": output.getvalue()}


def serve(a):
    # Persistent board on loopback. serve.port records the endpoint.
    # Each connection accepts a JSON line with argv/actor/project and returns
    # JSON stdout, amortizing process startup in active sessions.
    port_file = harness_ns["data_directory"]() / "serve.port"
    parser = build_parser()

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            try:
                request = json.loads(self.rfile.readline())
                if not isinstance(request, dict):
                    raise ValueError("expected a JSON object")
                response = execute_request(request, parser)
            except (ValueError, OSError) as exc:
                response = {"ok": False, "stdout": "", "error": str(exc)}
            self.wfile.write((json.dumps(response, ensure_ascii=False) + "\n").encode("utf8"))

    port_file.parent.mkdir(parents=True, exist_ok=True)
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    with tempfile.NamedTemporaryFile("w", encoding="utf8", dir=port_file.parent,
                                     delete=False) as handle:
        handle.write(str(server.server_address[1]))
        temp_port = Path(handle.name)
    temp_port.replace(port_file)
    print(json.dumps({"ready": True, "port": server.server_address[1]}, ensure_ascii=False), flush=True)

    def watch_stdin():
        for line in sys.stdin:
            if line.strip() == "quit":
                break
        server.shutdown()

    if not a.daemon:
        threading.Thread(target=watch_stdin, daemon=True).start()
    try:
        server.serve_forever()
    finally:
        server.server_close()
        try:
            if port_file.read_text(encoding="utf8").strip() == str(server.server_address[1]):
                port_file.unlink(missing_ok=True)
        except FileNotFoundError:
            pass


def run_rpc(a):
    argv = list(a.argv)
    if argv[:1] == ["--"]:
        argv = argv[1:]
    return hk.load("hk_call")["call"](harness_ns["data_directory"]() / "serve.port", a.project or Path.cwd(),
                                      a.actor or os.environ.get("CODEX_THREAD_ID"), argv)


def main():
    a = arguments()
    if a.cmd == "serve":
        return serve(a)
    if a.cmd == "rpc":
        return run_rpc(a)
    run(a, os.environ.get("CODEX_THREAD_ID"))


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except (ValueError, OSError, sqlite3.Error) as exc:
        sys.exit(str(exc))
