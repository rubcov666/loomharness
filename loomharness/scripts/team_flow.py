"""Domain ownership, independent review and serialized delivery on the local team board."""

import argparse
import json
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time

try:
    import hk
except ImportError:  # python -I исключает каталог скрипта из sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import hk

queue = hk.load("review_queue")
models = hk.load("team_models")

STAGES = {"review": "service/review", "delivery": "service/delivery"}
REVIEW_POOL = 2
SCHEMA = (
    "CREATE TABLE IF NOT EXISTS domains (name TEXT PRIMARY KEY, ownership TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS resources (task INTEGER REFERENCES tasks(id), name TEXT, PRIMARY KEY(task,name))",
    """CREATE TABLE IF NOT EXISTS submissions (task INTEGER PRIMARY KEY REFERENCES tasks(id),
       revision TEXT NOT NULL, artifact TEXT)""",
    """CREATE TABLE IF NOT EXISTS jobs (
       id INTEGER PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'offered',
       ticket TEXT NOT NULL, revision TEXT NOT NULL, target TEXT, action TEXT, authority TEXT, effects TEXT, note TEXT)""",
    # Выпуск остаётся строго последовательным; ревью допускает пул из двух
    # заданий, чтобы ревьюер работал, пока первое задание ждёт фоновый тест.
    """CREATE UNIQUE INDEX IF NOT EXISTS one_active_delivery ON jobs(kind)
       WHERE state IN ('offered','active') AND kind='delivery'""",
    """CREATE TABLE IF NOT EXISTS job_tasks (
       job INTEGER REFERENCES jobs(id), task INTEGER REFERENCES tasks(id), PRIMARY KEY(job,task))""",
)


def check(value, message):
    if not value:
        raise ValueError(message)


def enabled(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='domains'").fetchone() is not None


def revision(value):
    if not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", value):
        raise argparse.ArgumentTypeError("expected a full Git object ID or SHA-256")
    return value


def git_options(cmd):
    cmd.add_argument("--git", choices=("none", "existing"))
    cmd.add_argument("--git-dir", type=Path, help="existing checkout when different from project root")


def git_choice(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='git_policy'").fetchone():
        return None
    row = db.execute("SELECT mode,directory FROM git_policy WHERE id=1").fetchone()
    return dict(row) if row else None


def save_git(db, root, a):
    if not a.git:
        check(a.git_dir is None, "--git-dir needs --git existing")
        return git_choice(db)
    directory = None
    if a.git == "existing":
        where = (root / (a.git_dir or ".")).resolve(strict=True)
        result = subprocess.run(["git", "-C", str(where), "rev-parse", "--show-toplevel"],
                                capture_output=True, encoding="utf8", timeout=10)
        check(result.returncode == 0 and result.stdout.strip(), "select an existing Git checkout")
        directory = str(Path(result.stdout.strip()).resolve(strict=True))
    else:
        check(a.git_dir is None, "Git disabled; omit checkout directory")
    db.execute("CREATE TABLE IF NOT EXISTS git_policy(id INTEGER PRIMARY KEY, mode TEXT, directory TEXT)")
    db.execute("INSERT OR REPLACE INTO git_policy VALUES(1,?,?)", (a.git, directory))
    return git_choice(db)


def verify_candidate(db, root, ref, artifact):
    if artifact:
        path = (root / artifact).resolve(strict=True)
        check(path.is_relative_to(root) and path.is_file(), "use a regular candidate file inside the project")
        check(ref == hk.load("harness")["cached_digest"](path), "candidate file differs from submitted SHA-256")
        return str(path.relative_to(root))
    policy = git_choice(db)
    check(policy and policy["mode"] == "existing", "submit a snapshot file with --artifact for projects without Git")
    result = subprocess.run(["git", "-C", policy["directory"], "cat-file", "-t", ref],
                            capture_output=True, encoding="utf8", timeout=10)
    check(result.returncode == 0 and result.stdout.strip() == "commit", "expected an existing full commit ID")
    return None


def verify_job_files(db, root, job):
    for row in db.execute("""SELECT s.revision,s.artifact FROM job_tasks x JOIN submissions s ON x.task=s.task
                             WHERE x.job=?""", (job["id"],)):
        check(row["revision"] == job["revision"], "submission changed after assignment")
        verify_candidate(db, root, row["revision"], row["artifact"])


def has_review_items(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_items'").fetchone()


def ensure_review_items(db):
    if has_review_items(db):
        return
    db.execute("""CREATE TABLE review_items (
        job INTEGER, task INTEGER, revision TEXT NOT NULL, decision TEXT, note TEXT,
        PRIMARY KEY(job,task), FOREIGN KEY(job,task) REFERENCES job_tasks(job,task))""")
    db.execute("CREATE INDEX review_by_task ON review_items(task,revision,decision)")
    db.execute("""CREATE TABLE IF NOT EXISTS card_timings (
        task INTEGER PRIMARY KEY REFERENCES tasks(id), dispatched REAL NOT NULL, accepted REAL)""")
    # Before grouped reviews, each review job held one immutable revision and one verdict.
    db.execute("""INSERT INTO review_items
        SELECT j.id,x.task,j.revision,
               CASE j.state WHEN 'approved' THEN 'approve' WHEN 'changes' THEN 'changes' END,
               CASE WHEN j.state IN ('approved','changes') THEN j.note END
        FROM jobs j JOIN job_tasks x ON x.job=j.id WHERE j.kind='review'""")


def add_commands(cmds, text):
    setup = cmds.add_parser("organize", help="register domain ownership and enable independent review")
    setup.add_argument("file", type=Path, help="JSON object mapping domain names to responsibilities")
    setup.add_argument("--slots", type=int, required=True)
    git_options(setup)
    git_options(cmds.add_parser("git-choice", help="record the user's Git preference"))
    cmds.add_parser("roster")
    review = cmds.add_parser("review")
    review.add_argument("tasks", type=int, nargs="+")
    review.add_argument("--expect-revision", type=revision, action="append", metavar="SHA",
                        help="pin the submission seen in inbox; repeat in task order for a batch")
    delivery = cmds.add_parser("delivery")
    delivery.add_argument("tasks", type=int, nargs="+")
    delivery.add_argument("--revision", type=revision, required=True)
    delivery.add_argument("--action", choices=("prepare", "publish"), required=True)
    delivery.add_argument("--target", type=text, required=True)
    delivery.add_argument("--effects", choices=("handoff", "git", "service"), nargs="+", default=["handoff"])
    delivery.add_argument("--authority", type=text, help="reference to direct user authorization for publish")
    for name in ("job", "job-bind", "job-claim", "verdict", "verdicts", "delivered", "job-cancel"):
        parser = cmds.add_parser(name)
        parser.add_argument("id", type=int)
        if name in ("job-bind", "job-claim", "verdict", "verdicts", "delivered"):
            parser.add_argument("ticket", type=text)
        if name == "job-bind":
            parser.add_argument("thread", type=text)
        if name == "verdict":
            parser.add_argument("decision", choices=("approve", "changes"))
            parser.add_argument("--task", type=int, help="card ID inside a grouped review")
        if name == "verdicts":
            parser.add_argument("file", type=Path, help="JSON verdicts, or - for stdin")
        if name in ("verdict", "delivered"):
            parser.add_argument("--revision", type=revision, required=True)
        if name in ("verdict", "delivered", "job-cancel"):
            parser.add_argument("note", type=text)


def organize(db, p, root, a):
    check(p["mode"] == "roles" and a.slots > 0, "organization needs roles mode and positive slots")
    raw = sys.stdin.read() if str(a.file) == "-" else a.file.read_text(encoding="utf-8-sig")
    domains = json.loads(raw)
    check(isinstance(domains, dict) and domains and all(
        isinstance(v, str) and k.strip() == k and k and k not in STAGES.values() and v.strip()
        for k, v in domains.items()),
        "provide named domains and their responsibilities")
    # Миграция: снять старый индекс серийного сервиса, выпуск сериализуется
    # новым partial-индексом. Возможно только при пустой очереди заданий.
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='one_active_service'").fetchone():
        db.execute("DROP INDEX one_active_service")
    for sql in SCHEMA:
        db.execute(sql)
    old = {r[0] for r in db.execute("SELECT name FROM domains")}
    check(old <= domains.keys(), "keep existing domain names to preserve ownership; edit their descriptions")
    active = list(db.execute("SELECT role,state FROM tasks WHERE state NOT IN ('accepted','open')"))
    check(not active, "finish active assignments before changing the organization")
    check(not db.execute("SELECT 1 FROM jobs WHERE state IN ('offered','active')").fetchone(), "service chat busy")
    open_roles = {r[0] for r in db.execute("SELECT DISTINCT role FROM tasks WHERE state='open'")}
    check(open_roles <= domains.keys(), "register all open cards' owners first")
    policy = save_git(db, root, a)
    check(policy, "ask the owner whether this project uses Git and record --git")
    db.execute("""INSERT INTO resources SELECT id,'*' FROM tasks WHERE state='open'
                  AND NOT EXISTS(SELECT 1 FROM resources WHERE task=tasks.id)""")
    db.executemany("INSERT INTO domains VALUES(?,?) ON CONFLICT(name) DO UPDATE SET ownership=excluded.ownership",
                   domains.items())
    db.execute("UPDATE project SET slots=?", (a.slots,))
    return {"domains": list(domains), "slots": a.slots, "review_model": p["strong_model"], "git": policy}


def roster(db):
    return [dict(row) for row in db.execute("""SELECT name,ownership,thread FROM domains
             LEFT JOIN chats ON slot='role/'||name ORDER BY name""")]


def conflicts(db, task):
    return db.execute("""SELECT DISTINCT t.id FROM resources mine JOIN resources other
              ON mine.name=other.name OR mine.name='*' OR other.name='*'
              JOIN tasks t ON other.task=t.id WHERE mine.task=? AND mine.task<>other.task
              AND t.state IN ('reserved','working')""", (task,)).fetchall()


def job_info(db, job, lead):
    record = dict(job)
    if not lead:
        record.pop("ticket")
    record["tasks"] = [r[0] for r in db.execute("SELECT task FROM job_tasks WHERE job=? ORDER BY task", (job["id"],))]
    if job["kind"] == "review" and has_review_items(db):
        record["items"] = [dict(r) for r in db.execute(
            "SELECT task,revision,decision,note FROM review_items WHERE job=? ORDER BY task", (job["id"],))]
        if len(record["tasks"]) > 1:
            record["revision"] = None
    chat = db.execute("SELECT thread FROM chats WHERE slot=?", (STAGES[job["kind"]],)).fetchone()
    record["thread"] = chat[0] if chat else None
    if job["kind"] == "review" and (run := queue["current"](db, job["id"])):
        record["test"] = run
    return record


def review_candidates(db, project):
    if project["paused"]:
        return []
    active = db.execute("SELECT count(*) FROM jobs WHERE kind='review' AND state IN ('offered','active')"
                        ).fetchone()[0]
    if active >= REVIEW_POOL:
        return []
    # Карточки уже в активном задании не предлагаем повторно: пул пополняется
    # только ожидающими карточками.
    items = "review_items" if has_review_items(db) else "job_tasks"
    pending = "AND x.decision IS NULL AND x.revision=s.revision" if items == "review_items" else ""
    rows = db.execute(f"""SELECT t.id,s.revision FROM tasks t JOIN submissions s ON s.task=t.id
                          WHERE t.state='review' AND NOT EXISTS (
                              SELECT 1 FROM {items} x JOIN jobs j ON j.id=x.job
                              WHERE x.task=t.id AND j.kind='review' AND j.state IN ('offered','active') {pending})
                          ORDER BY t.id LIMIT 4""")
    return [dict(row) for row in rows]


def ready_reviews(db, project):
    return [row["id"] for row in review_candidates(db, project)]


def assign(db, p, root, a):
    kind = a.cmd
    if kind == "review":
        active = db.execute("SELECT count(*) FROM jobs WHERE kind='review' AND state IN ('offered','active')"
                            ).fetchone()[0]
        check(active < REVIEW_POOL, "review pool is full; current jobs are visible in plan")
    else:
        check(not db.execute("SELECT 1 FROM jobs WHERE kind=? AND state IN ('offered','active')", (kind,)).fetchone(),
              "service chat already assigned; current job is visible in plan")
    target = action = authority = None
    effects = ""
    ids = sorted(set(a.tasks))
    if kind == "review":
        expected = {}
        if a.expect_revision:
            check(len(a.expect_revision) == len(a.tasks) == len(ids), "supply one expected revision per distinct task")
            expected = dict(zip(a.tasks, a.expect_revision))
        cards = []
        for tid in ids:
            card = db.execute("""SELECT t.id,title,role,scope,criterion,evidence,revision,artifact FROM tasks t
                                 JOIN submissions s ON s.task=t.id WHERE t.id=? AND t.state='review'""", (tid,)).fetchone()
            check(card, f"task {tid} needs submitted evidence and a candidate revision")
            check(not expected or card["revision"] == expected[tid], f"task {tid} changed revision; read inbox again")
            occupied = db.execute("""SELECT x.job FROM review_items x JOIN jobs j ON j.id=x.job WHERE x.task=?
                                     AND x.revision=? AND x.decision IS NULL AND j.state IN ('offered','active')""",
                                  (tid, card["revision"])).fetchone()
            if occupied:
                raise ValueError(f"task {tid} already assigned to review job {occupied[0]}")
            cards.append(dict(card))
        ref = cards[0]["revision"] if len(cards) == 1 else ""
        brief = {"card": cards[0]} if len(cards) == 1 else {"cards": cards}
        brief["on_idle"] = "Finish the turn after reporting a verdict or blocker. Resume on a concrete assignment."
    else:
        ref, target, action, authority = a.revision, a.target, a.action, a.authority
        policy = git_choice(db)
        check("git" not in a.effects or (policy and policy["mode"] == "existing"), "Git disabled for this project")
        effects = ",".join(sorted(set(a.effects)))
        check(action != "publish" or authority, "publish needs the user's release instruction")
        notes = []
        for tid in ids:
            approved = db.execute("""SELECT r.note,t.title,t.evidence,s.artifact FROM review_items r
                JOIN tasks t ON t.id=r.task JOIN submissions s ON s.task=t.id WHERE r.task=? AND r.decision='approve'
                AND t.state='accepted' AND r.revision=s.revision AND s.revision=?
                ORDER BY r.job DESC LIMIT 1""", (tid, ref)).fetchone()
            check(approved, f"task {tid} needs independent approval for this exact candidate")
            verify_candidate(db, root, ref, approved["artifact"])
            notes.append(dict(approved, task=tid))
        done = db.execute("""SELECT 1 FROM jobs WHERE kind='delivery' AND state='done' AND revision=?
                             AND target=? AND action=? AND effects=?""", (ref, target, action, effects)).fetchone()
        check(not done, "this candidate already completed this operation on this target")
        brief = {"approvals": notes, "target": target, "action": action, "authority": authority,
                 "effects": effects.split(","), "git": policy}
    ticket = secrets.token_hex(10)
    jid = db.execute("INSERT INTO jobs(kind,ticket,revision,target,action,authority,effects) VALUES(?,?,?,?,?,?,?)",
                     (kind,ticket,ref,target,action,authority,effects)).lastrowid
    db.executemany("INSERT INTO job_tasks VALUES(?,?)", [(jid, tid) for tid in ids])
    if kind == "review":
        db.executemany("INSERT INTO review_items(job,task,revision) VALUES(?,?,?)",
                       [(jid, card["id"], card["revision"]) for card in cards])
    owner = db.execute("SELECT thread FROM chats WHERE slot=?", (STAGES[kind],)).fetchone()
    brief.update(project=str(root), job=jid, ticket=ticket, kind=kind)
    if ref:
        brief["revision"] = ref
    strong = kind == "review" or (action == "publish" and effects != "handoff")
    return {"operation": "send_message_to_thread" if owner else "create_thread",
            "threadId": owner[0] if owner else None, "model": p["strong_model" if strong else "simple_model"],
            "thinking": "medium" if strong else "low", "brief": brief}


def record_verdicts(db, root, job, a):
    check(job["kind"] == "review", "expected review job")
    items = {r["task"]: r for r in db.execute("SELECT * FROM review_items WHERE job=?", (job["id"],))}
    if a.cmd == "verdicts":
        raw = sys.stdin.read() if str(a.file) == "-" else a.file.read_text(encoding="utf-8-sig")
        decisions = json.loads(raw)
        check(isinstance(decisions, list) and decisions, "provide a nonempty JSON array of verdicts")
    else:
        check(a.task is not None or len(items) == 1, "select a card with --task or submit verdicts")
        tid = a.task if a.task is not None else next(iter(items))
        decisions = [dict(task=tid, revision=a.revision, decision=a.decision, note=a.note)]
    seen = set()
    for decision in decisions:
        check(isinstance(decision, dict) and set(decision) == {"task","revision","decision","note"},
              "each verdict needs task, revision, decision and note")
        tid, ref, vote, note = (decision[k] for k in ("task","revision","decision","note"))
        check(type(tid) is int and tid in items and tid not in seen, "unknown or duplicate card in review")
        seen.add(tid)
        item = items[tid]
        check(ref == item["revision"], "candidate changed; review the assigned version")
        check(vote in ("approve","changes") and isinstance(note, str) and note.strip(), "decision and evidence required")
        if item["decision"]:
            check((item["decision"], item["note"]) == (vote, note), "a different verdict was already recorded")
            continue
        check(job["state"] == "active", "claim service job first")
        submission = db.execute("""SELECT s.artifact FROM submissions s JOIN tasks t ON s.task=t.id
                                   WHERE s.task=? AND s.revision=? AND t.state='review'""", (tid,ref)).fetchone()
        check(submission, "submission no longer current")
        approve = vote == "approve"
        run = queue["current"](db, a.id)
        if run and run["task"] == tid:
            check(run["status"] != "running", "background test is still running")
            check(not approve or run["status"] == "passed", "successful background test required")
        if approve:
            verify_candidate(db, root, ref, submission["artifact"])
            db.execute("UPDATE card_timings SET accepted=? WHERE task=?", (time.time(),tid))
        else:
            models["returned"](db,tid)
        db.execute("UPDATE tasks SET state=?,review=?,returns=returns+? WHERE id=?",
                   ("accepted" if approve else "open", note, int(not approve), tid))
        db.execute("UPDATE review_items SET decision=?,note=? WHERE job=? AND task=?", (vote,note,a.id,tid))
    state = job["state"]
    if state == "active":
        votes = {r[0] for r in db.execute("SELECT decision FROM review_items WHERE job=?", (a.id,))}
        if None not in votes:
            state = "mixed" if len(votes) > 1 else ("approved" if "approve" in votes else "changes")
            db.execute("UPDATE jobs SET state=?,note=? WHERE id=?",
                       (state, decisions[0]["note"] if len(items) == 1 else None, a.id))
    result = {"job": a.id, "state": state}
    if len(items) > 1:
        result["decisions"] = {r["task"]:r["decision"] for r in db.execute(
            "SELECT task,decision FROM review_items WHERE job=? ORDER BY task", (a.id,))}
    return result


def service(db, p, root, a, actor):
    lead = actor == p["lead"]
    if a.cmd == "roster":
        return roster(db)
    if a.cmd in ("review", "delivery"):
        check(lead, "only the coordinator dispatches service jobs")
        ensure_review_items(db)
        return assign(db, p, root, a)
    job = db.execute("SELECT * FROM jobs WHERE id=?", (a.id,)).fetchone()
    check(job, "unknown service job")
    if a.cmd == "job":
        return job_info(db, job, lead)
    if a.cmd == "review-side":
        check(lead and job["kind"]=="review" and job["state"]=="active","active review and coordinator required")
        return queue["side"](db,p,a)
    if a.cmd == "job-cancel":
        check(lead and job["state"] in ("offered","active"), "only the coordinator can reconcile an outstanding job")
        run = queue["current"](db,a.id)
        check(not run or run["status"]!="running","reconcile the background test before cancellation")
        db.execute("UPDATE jobs SET state='cancelled',note=? WHERE id=?", (a.note,a.id))
        return {"job": a.id, "state": "cancelled"}
    check(job["ticket"] == a.ticket, "stale service assignment")
    slot = STAGES[job["kind"]]
    owner = db.execute("SELECT thread FROM chats WHERE slot=?", (slot,)).fetchone()
    if a.cmd == "review-interrupt":
        check(job["kind"]=="review" and job["state"]=="active" and (lead or (owner and owner[0]==actor)),
              "reviewer or coordinator required")
        run=queue["current"](db,a.id)
        check(run and run["run"]==a.run and run["status"]=="running" and time.time()>=run["due"],
              "reconcile only the overdue current run")
        db.execute("UPDATE review_runs SET status='interrupted',note=? WHERE job=?", (a.note,a.id))
        return {"job":a.id,"status":"interrupted"}
    if a.cmd in ("job-bind","job-claim"):
        bind = a.cmd == "job-bind"
        check(lead if bind else not lead, "wrong actor for service assignment")
        who = a.thread if bind else actor
        check(who != p["lead"] and (not owner or owner[0] == who), "different service owner")
        check(job["state"] == "offered" or (bind and owner), "service assignment no longer available")
        if not owner:
            db.execute("INSERT INTO chats VALUES(?,?)", (slot, who))
        if not bind:
            if job["kind"] == "delivery":
                verify_job_files(db, root, job)
            db.execute("UPDATE jobs SET state='active' WHERE id=?", (a.id,))
            if job["kind"] == "review":
                return {"job": a.id, "thread": who,
                        "background_test": "For a long check, run review-test JOB TICKET CARD -- PROGRAM ARGS. You can take one short independent card in the same job while it runs."}
        return {"job": a.id, "thread": who}
    check(owner and owner[0] == actor and not lead, "claim service job first")
    if a.cmd in ("verdict", "verdicts"):
        ensure_review_items(db)
        return record_verdicts(db, root, job, a)
    check(job["state"] == "active", "claim service job first")
    check(job["revision"] == a.revision, "candidate changed; review the new version")
    check(a.cmd == "delivered" and job["kind"] == "delivery", "expected delivery job")
    verify_job_files(db, root, job)
    db.execute("UPDATE jobs SET state='done',note=? WHERE id=?", (a.note,a.id))
    return {"job": a.id, "state": "done"}
