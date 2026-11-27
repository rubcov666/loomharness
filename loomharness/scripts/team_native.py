"""Route LoomHarness board assignments to named native agents."""

from pathlib import Path
import argparse
from contextlib import closing
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import time


DEFAULT_SLOTS = 2
MAX_SLOTS = 3
SERVICE_ROLES = {"review": "reviewer", "delivery": "release"}
try:
    import hk
except ImportError:  # python -I excludes the script directory from sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import hk


team = hk.load("team")
flow = team["flow"]
original_operate = team["operate"]
service_actions = {"job-bind", "job-claim", "verdict", "verdicts", "delivered", "job"}


def require(value, message):
    if not value:
        raise ValueError(message)


def table_exists(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def saved_role(process):
    kind, _, value = process.partition("/")
    if kind == "role":
        return value
    return "developer" if kind == "dispatch" else SERVICE_ROLES.get(kind, kind)


def ensure_native_services(db):
    schema = "CREATE TABLE native_services (job INTEGER PRIMARY KEY REFERENCES jobs(id), actor TEXT)"
    if not table_exists(db, "native_services"):
        db.execute(schema)
    elif any(r["unique"] for r in db.execute("PRAGMA index_list(native_services)")):
        db.execute("ALTER TABLE native_services RENAME TO native_services_legacy")
        db.execute(schema)
        db.execute("INSERT INTO native_services(job,actor) SELECT job,actor FROM native_services_legacy")
        db.execute("DROP TABLE native_services_legacy")
    db.execute("CREATE INDEX IF NOT EXISTS native_services_actor ON native_services(actor)")


def previous_route(db, actor):
    if not table_exists(db, "native_routes"):
        return None
    return db.execute("SELECT process,model,thinking FROM native_routes WHERE actor=? ORDER BY rowid DESC LIMIT 1", (actor,)).fetchone()


def named_role(db, ticket):
    if table_exists(db, "native_routes"):
        record = db.execute("SELECT process FROM native_routes WHERE ticket=?", (ticket,)).fetchone()
        if record and record[0].startswith("role/"):
            return saved_role(record[0])
    return None


def record_route(db, ticket, process, model, thinking, actor):
    db.execute("""CREATE TABLE IF NOT EXISTS native_routes (
        ticket TEXT PRIMARY KEY, process TEXT NOT NULL, model TEXT, thinking TEXT, actor TEXT)""")
    db.execute("CREATE INDEX IF NOT EXISTS native_routes_actor ON native_routes(actor)")
    db.execute("INSERT INTO native_routes VALUES (?,?,?,?,?)", (ticket, process, model, thinking, actor))


def bind_route(db, ticket, actor):
    if table_exists(db, "native_routes"):
        db.execute("UPDATE native_routes SET actor=? WHERE ticket=? AND actor IS NULL", (actor, ticket))


def agent_is_busy(db, actor):
    if db.execute("""SELECT 1 FROM tasks t JOIN chats c ON c.slot=t.slot
        WHERE c.thread=? AND t.state IN ('reserved','working')""", (actor,)).fetchone():
        return True
    return table_exists(db, "native_services") and db.execute("""SELECT 1 FROM native_services n JOIN jobs j ON j.id=n.job
        WHERE n.actor=? AND j.state IN ('offered','active')""", (actor,)).fetchone() is not None


def select_agent(db, role, model, thinking, args):
    name = f"/root/{role}"
    active = []
    if table_exists(db, "native_routes"):
        active = [r[0] for r in db.execute("""SELECT r.process FROM native_routes r JOIN tasks t ON t.ticket=r.ticket
            WHERE t.state IN ('reserved','working')""")]
        if table_exists(db, "jobs"):
            active.extend(r[0] for r in db.execute("""SELECT r.process FROM native_routes r JOIN jobs j ON j.ticket=r.ticket
                WHERE j.state IN ('offered','active')"""))
    require(not any(saved_role(process) == role for process in active),
            f"{role} is already assigned; wait for the role to finish")
    if args.reuse:
        require(args.reuse == name, f"use the named agent {name} for this role")
    old = previous_route(db, name)
    if args.reuse or name in (args.available or []):
        require(old and saved_role(old["process"]) == role, "bind the named agent before reusing it")
        require(not agent_is_busy(db, name), f"{role} is already assigned; wait for the role to finish")
        require((old["model"], old["thinking"]) == (model, thinking),
                f"{role} needs a different model or effort; close the idle session before replacing it with the same name")
        return name
    require(not old or args.available is not None,
            f"check the live {role} roster: pass --available {name}, or confirm it is absent")
    return None


def route_assignment(db, kind, assignment, args):
    brief = assignment["brief"]
    ticket = brief["ticket"]
    role = (args.agent_role or "developer") if kind == "dispatch" else SERVICE_ROLES[kind]
    model, thinking = assignment["model"], assignment["thinking"]
    reuse = select_agent(db, role, model, thinking, args)
    if kind == "dispatch":
        slot = f"agent/task/{brief['id']}/{ticket}"
        if reuse:
            owner = db.execute("SELECT slot FROM chats WHERE thread=?", (reuse,)).fetchone()
            require(owner and owner[0].startswith("agent/task/"), "reuse requires a bound native worker")
            slot = owner[0]
        db.execute("UPDATE tasks SET slot=? WHERE id=?", (slot, brief["id"]))
    else:
        ensure_native_services(db)
        db.execute("INSERT INTO native_services(job,actor) VALUES (?,?)", (brief["job"], reuse))
    brief["agent_role"] = role
    record_route(db, ticket, f"role/{role}", model, thinking, reuse)
    if reuse:
        if kind == "dispatch" and not args.full_brief and not args.brief:
            brief["prerequisites"] = [{"id": dep["id"], "state": dep["state"]} for dep in brief["prerequisites"]]
        return dict(assignment, operation="send_input", threadId=reuse, process=role, roleLabel=role.replace("_", " ").title())
    return dict(assignment, operation="spawn_agent", threadId=None, process=role,
                taskName=role, roleLabel=role.replace("_", " ").title())


def active_slots(db):
    workers = [r[0] for r in db.execute("SELECT id FROM tasks WHERE state IN ('reserved','working')")]
    if not flow["enabled"](db):
        return len(workers), workers
    active = "j.state IN ('offered','active')"
    jobs = db.execute(f"SELECT count(*) FROM jobs j WHERE {active}").fetchone()[0]
    tasks = [r[0] for r in db.execute(f"""SELECT x.task FROM job_tasks x
                JOIN jobs j ON j.id=x.job WHERE {active}""")]
    return len(workers) + jobs, workers + tasks


def third_slot_reason(db, reason, candidate_ids, active_ids):
    require(reason and reason.strip(), "give --third-slot a concrete reason for starting now")
    require(candidate_ids and active_ids and flow["enabled"](db), "third slot needs declared resource scopes")
    resources = {}
    for tid in set(candidate_ids) | set(active_ids):
        resources[tid] = {r[0] for r in db.execute("SELECT name FROM resources WHERE task=?", (tid,))}
        require(resources[tid] and "*" not in resources[tid], "third slot needs a specific resource scope for every active card")
    used = set.union(*(resources[tid] for tid in active_ids))
    require(all(not (resources[tid] & used) for tid in candidate_ids), "third slot would overlap an active resource")
    return reason.strip()


def record_extra_slot(db, cmd, assignment, reason):
    brief = assignment["brief"]
    ref = brief["id"] if cmd == "dispatch" else brief["job"]
    db.execute("""CREATE TABLE IF NOT EXISTS native_extra_slots (
        id INTEGER PRIMARY KEY, at REAL NOT NULL, kind TEXT NOT NULL,
        assignment INTEGER NOT NULL, reason TEXT NOT NULL)""")
    db.execute("INSERT INTO native_extra_slots(at,kind,assignment,reason) VALUES(?,?,?,?)", (time.time(), cmd, ref, reason))


def review_batch(db, project):
    if project["paused"] or not flow["enabled"](db):
        return []
    active = db.execute("SELECT count(*) FROM jobs WHERE kind='review' AND state IN ('offered','active')").fetchone()[0]
    if active:
        return []
    short = """EXISTS (SELECT 1 FROM short_reviews q WHERE q.task=t.id AND q.revision=s.revision)"""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='short_reviews'").fetchone():
        short = "0"
    items = "review_items" if flow["has_review_items"](db) else "job_tasks"
    current = "AND item.revision=s.revision AND item.decision IS NULL" if items == "review_items" else ""
    rows = db.execute(f"""SELECT t.id,s.revision,t.role,{short} AS quick,
          EXISTS (SELECT 1 FROM deps d JOIN tasks child ON child.id=d.task
                  WHERE d.needs=t.id AND child.state='open') AS blocking
        FROM tasks t JOIN submissions s ON s.task=t.id
        WHERE t.state='review' AND NOT EXISTS (
          SELECT 1 FROM {items} item JOIN jobs j ON j.id=item.job
          WHERE item.task=t.id AND j.kind='review' AND j.state IN ('offered','active') {current})
        ORDER BY blocking DESC,t.id""").fetchall()
    if not rows:
        return []
    first, *rest = rows
    batch = [first]
    if not first["blocking"]:
        related = [r for r in rest if not r["blocking"] and (
            r["revision"] == first["revision"] or (first["quick"] and r["quick"] and r["role"] == first["role"])
        )]
        related.sort(key=lambda r: r["revision"] != first["revision"])
        batch.extend(related[:2])
    return [{"id": r["id"], "revision": r["revision"], "role": r["role"]} for r in batch]


def status_snapshot(db, project, ids, stopped):
    db.execute("BEGIN")
    with db:
        p = db.execute("SELECT * FROM project WHERE id=1").fetchone()
        require(p, "no team board for this project")
        counts = dict(db.execute("SELECT state,count(*) FROM tasks GROUP BY state"))
        workers = [list(r) for r in db.execute("""SELECT t.id,t.state,c.thread FROM tasks t
                   LEFT JOIN chats c ON c.slot=t.slot WHERE t.state IN ('reserved','working') ORDER BY t.id""")]
        state = {"stopped": stopped, "paused": bool(p["paused"]), "counts": counts,
                 "workers": workers, "reviews": [] if stopped else review_batch(db, p)}
        if flow["enabled"](db):
            native = db.execute("SELECT 1 FROM sqlite_master WHERE name='native_services'").fetchone()
            owner = "CASE WHEN n.job IS NULL THEN c.thread ELSE n.actor END" if native else "c.thread"
            join = "LEFT JOIN native_services n ON n.job=j.id" if native else ""
            state["jobs"] = [list(r) for r in db.execute(f"""SELECT j.id,j.kind,j.state,{owner}
                FROM jobs j LEFT JOIN chats c ON c.slot='service/'||j.kind {join}
                WHERE j.state IN ('offered','active') ORDER BY j.id""")]
        if ids:
            available = "s.revision" if flow["enabled"](db) else "NULL"
            join = "LEFT JOIN submissions s ON s.task=t.id" if available != "NULL" else ""
            markers = ",".join("?" for _ in ids)
            state["cards"] = [dict(r) for r in db.execute(f"""SELECT t.id,t.state,t.role,{available} AS revision
                FROM tasks t {join} WHERE t.id IN ({markers}) ORDER BY t.id""", ids)]
        state["slots"] = {"active": len(workers) + len(state.get("jobs", [])), "default": DEFAULT_SLOTS, "maximum": MAX_SLOTS}
    cursor = hashlib.sha256(json.dumps([str(project), state], sort_keys=True).encode()).hexdigest()
    return dict(changed=True, cursor=cursor, **state)


def read_status(root, args):
    require(math.isfinite(args.wait) and 0 <= args.wait <= 60, "use --wait between 0 and 60 seconds")
    require(not args.wait or args.after, "start status --wait with the previous --after cursor")
    path = team["board_path"](root)
    require(path.is_file(), "no team board for this project")
    deadline = time.monotonic() + args.wait
    previous = None
    with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True, timeout=8)) as db:
        db.row_factory = sqlite3.Row
        while True:
            stopped = (root / "AGENT_STOP").exists()
            version = (db.execute("PRAGMA data_version").fetchone()[0], stopped)
            if version != previous:
                previous = version
                snapshot = status_snapshot(db, root, args.ids, stopped)
                if snapshot["cursor"] != args.after:
                    return snapshot
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {"changed": False, "cursor": args.after}
            time.sleep(min(0.25, remaining))


def native_service(db, root, args, actor, lead, job, owner):
    if args.cmd == "job":
        return dict(flow["job_info"](db, job, lead), thread=owner, operation="spawn_agent")
    require(job["ticket"] == args.ticket, "stale service assignment")
    if args.cmd == "job-bind":
        require(lead and args.thread != actor, "bind an independent subagent from the coordinator")
        if owner == args.thread:
            return {"job": args.id, "thread": owner}
        require(not owner and job["state"] == "offered", "service job is already assigned")
        require(not db.execute("SELECT 1 FROM chats WHERE thread=?", (args.thread,)).fetchone(),
                "service subagent must be independent of the worker and legacy service chats")
        role = named_role(db, args.ticket)
        if role:
            require(args.thread == f"/root/{role}", f"bind the single {role} agent by its exact name")
            require(not agent_is_busy(db, args.thread), f"{role} is already assigned")
        else:
            old_service = db.execute("SELECT 1 FROM native_services WHERE actor=?", (args.thread,)).fetchone()
            require(not previous_route(db, args.thread) and not old_service,
                    "continue a registered agent with --reuse at reservation")
        db.execute("UPDATE native_services SET actor=? WHERE job=?", (args.thread, args.id))
        bind_route(db, args.ticket, args.thread)
        return {"job": args.id, "thread": args.thread}
    require(owner and owner == actor and not lead, "only the bound subagent can act on this job")
    if args.cmd == "job-claim":
        if job["state"] == "offered":
            if job["kind"] == "delivery":
                flow["verify_job_files"](db, root, job)
            db.execute("UPDATE jobs SET state='active' WHERE id=?", (args.id,))
        else:
            require(job["state"] == "active", "service job is closed")
        return {"job": args.id, "thread": owner, "state": "active"}
    if args.cmd in {"verdict", "verdicts"}:
        require(job["state"] in {"active", "approved", "changes", "mixed"}, "claim service job first")
        return flow["record_verdicts"](db, root, job, args)
    require(job["kind"] == "delivery" and job["revision"] == args.revision, "invalid delivery revision")
    if job["state"] != "done":
        require(job["state"] == "active", "claim delivery job first")
        flow["verify_job_files"](db, root, job)
        db.execute("UPDATE jobs SET state='done',note=? WHERE id=?", (args.note, args.id))
    else:
        require(job["note"] == args.note, "a different delivery result was recorded")
    return {"job": args.id, "state": "done"}


def operate(db, root, args, actor):
    if args.cmd == "init":
        return original_operate(db, root, args, actor)
    project = db.execute("SELECT * FROM project WHERE id=1").fetchone()
    if not project:
        return original_operate(db, root, args, actor)
    cmd = args.cmd
    lead = actor == project["lead"]
    review_next = cmd == "review-next"
    if cmd in {"review-next", "dispatch", "review", "delivery"}:
        require(lead and not project["paused"], "active coordinator required")
        active_count, active_ids = active_slots(db)
        reason = getattr(args, "third_slot", None)
        if active_count >= MAX_SLOTS or (active_count >= DEFAULT_SLOTS and not reason):
            if review_next:
                return {"operation": "wait", "reason": "agent slot limit", "active": active_count}
            raise ValueError(f"agent slot limit: {active_count} active; wait for the next completion or request a justified third slot")
    if review_next:
        batch = review_batch(db, project)
        if not batch:
            return {"operation": "wait"}
        args.cmd = cmd = "review"
        args.tasks = [r["id"] for r in batch]
        args.expect_revision = [r["revision"] for r in batch]
    if cmd in {"dispatch", "review", "delivery"}:
        extra = None
        if active_count == DEFAULT_SLOTS:
            extra = third_slot_reason(db, reason, [args.id] if cmd == "dispatch" else args.tasks, active_ids)
        if cmd == "dispatch":
            require(not team["current_driver"](db), "set the stock team driver to desktop before using native agents")
            full_requested = args.full_brief
            args.full_brief = not args.brief
        if cmd == "review":
            require(args.expect_revision, "use --expect-revision for each card from inbox --reviews")
        assignment = original_operate(db, root, args, actor)
        if cmd == "dispatch":
            args.full_brief = full_requested
        assignment = route_assignment(db, cmd, assignment, args)
        if extra:
            record_extra_slot(db, cmd, assignment, extra)
        return assignment
    if cmd in {"bind", "claim"}:
        slot = db.execute("SELECT slot FROM tasks WHERE id=?", (args.id,)).fetchone()
        if slot and (slot[0] or "").startswith("agent/task/"):
            owner = db.execute("SELECT thread FROM chats WHERE slot=?", (slot[0],)).fetchone()
            if cmd == "claim":
                require(owner and owner[0] == actor, "coordinator must bind the new subagent before claim")
            else:
                if not owner:
                    role = named_role(db, args.ticket)
                    if role:
                        require(args.thread == f"/root/{role}", f"bind the single {role} agent by its exact name")
                        require(not agent_is_busy(db, args.thread), f"{role} is already assigned")
                        previous_slot = db.execute("SELECT slot FROM chats WHERE thread=?", (args.thread,)).fetchone()
                        if previous_slot:
                            require(previous_slot[0].startswith("agent/task/"), "expected a native worker")
                            db.execute("UPDATE tasks SET slot=? WHERE id=?", (previous_slot[0], args.id))
                    else:
                        require(not previous_route(db, args.thread), "continue an existing agent with --reuse at reservation")
                    used_for_service = table_exists(db, "native_services") and db.execute(
                        "SELECT 1 FROM native_services WHERE actor=?", (args.thread,)).fetchone()
                    require(not used_for_service, "service agents must remain independent of workers")
                result = original_operate(db, root, args, actor)
                bind_route(db, args.ticket, args.thread)
                return result
    if cmd == "next-stage":
        slot = db.execute("SELECT slot FROM tasks WHERE id=?", (args.id,)).fetchone()
        if slot and (slot[0] or "").startswith("agent/task/"):
            result = original_operate(db, root, args, actor)
            if result["operation"] != "wait":
                current = previous_route(db, result["threadId"])
                require(not current or (current["model"], current["thinking"]) == (result["model"], result["thinking"]),
                        "next stage needs a different model or reasoning effort; reserve a new agent")
                result["operation"] = "send_input"
            return result
    if cmd in service_actions and table_exists(db, "native_services"):
        native = db.execute("SELECT actor FROM native_services WHERE job=?", (args.id,)).fetchone()
        if native:
            require(actor, "supply the current Codex actor")
            require(cmd == "job" or not project["paused"], "project paused")
            job = db.execute("SELECT * FROM jobs WHERE id=?", (args.id,)).fetchone()
            return native_service(db, root, args, actor, lead, job, native[0])
    return original_operate(db, root, args, actor)


def worker_role(value):
    if not re.fullmatch(r"[a-z][a-z_]{0,31}", value) or value in {"root", *SERVICE_ROLES.values()}:
        raise argparse.ArgumentTypeError("use a role name such as developer, tester, or designer")
    return value


def arguments(argv=None):
    prefix = argparse.ArgumentParser(add_help=False)
    prefix.add_argument("--project", type=Path, default=Path.cwd())
    prefix.add_argument("--actor", dest="actor_id")
    prefix.add_argument("--third-slot", metavar="REASON")
    prefix.add_argument("--agent-role", type=worker_role)
    routing = prefix.add_mutually_exclusive_group()
    routing.add_argument("--reuse", metavar="AGENT")
    routing.add_argument("--available", action="append", metavar="AGENT")
    options, rest = prefix.parse_known_args(argv)
    if rest and rest[0] in {"status", "review-next"}:
        command = argparse.ArgumentParser(prog=f"{Path(__file__).name} {rest[0]}")
        if rest[0] == "status":
            command.add_argument("ids", nargs="*", type=int)
            command.add_argument("--after")
            command.add_argument("--wait", type=float, default=0)
        options.cmd = rest[0]
        return command.parse_args(rest[1:], namespace=options)
    parser = team["build_parser"]()
    parser.epilog = "Native coordinator commands: status [CARD ...] [--after CURSOR --wait SECONDS], review-next. Pass --available AGENT for each live idle agent, or --available none. Dispatch can select a work role such as --agent-role designer. Use --third-slot REASON for an exceptional independent assignment."
    return parser.parse_args(rest, namespace=options)


def main(argv=None):
    args = arguments(argv)
    root = args.project.resolve(strict=True)
    require(root.is_dir(), "project must be a directory")
    require(args.cmd not in {"serve", "rpc"}, "use the local adapter directly for native assignments")
    require(not args.agent_role or args.cmd == "dispatch", "use --agent-role only with dispatch")
    require(not (args.reuse or args.available) or args.cmd in {"dispatch", "review", "review-next", "delivery"},
            "use agent routing options only when reserving a new assignment")
    if args.available and "none" in args.available:
        require(args.available == ["none"], "use --available none by itself")
        args.available = []
    if args.cmd == "status":
        print(json.dumps(read_status(root, args), ensure_ascii=False, separators=(",", ":")))
        return
    team["operate"] = operate
    team["run"](args, args.actor_id or os.environ.get("CODEX_THREAD_ID"))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, sqlite3.Error) as error:
        sys.exit(str(error))
