"""Finite domain surveys and owner decisions about project outcomes."""

import argparse
import json
import math
from pathlib import Path
import sys


COMMANDS = {"survey", "pulse", "proposal", "decide", "survey-close"}
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS surveys(id INTEGER PRIMARY KEY, milestone TEXT UNIQUE NOT NULL,
       criterion TEXT NOT NULL, closure TEXT)""",
    "CREATE UNIQUE INDEX IF NOT EXISTS one_survey ON surveys((1)) WHERE closure IS NULL",
    """CREATE TABLE IF NOT EXISTS survey_cards(survey INTEGER REFERENCES surveys, role TEXT,
       task INTEGER UNIQUE REFERENCES tasks, prior_lease TEXT, PRIMARY KEY(survey,role))""",
    """CREATE TABLE IF NOT EXISTS proposals(id INTEGER PRIMARY KEY, survey INTEGER REFERENCES surveys,
       question TEXT, sources TEXT, options TEXT, recommended TEXT, state TEXT DEFAULT 'pending',
       selection TEXT, authority TEXT, UNIQUE(survey,question))""",
    "CREATE UNIQUE INDEX IF NOT EXISTS one_pending_question ON proposals(survey) WHERE state='pending'",
    """CREATE TABLE IF NOT EXISTS approved_work(task INTEGER PRIMARY KEY REFERENCES tasks,
       proposal INTEGER REFERENCES proposals)""",
    "CREATE TABLE IF NOT EXISTS direct_work(task INTEGER PRIMARY KEY REFERENCES tasks, authority TEXT NOT NULL)",
)


def check(condition, message):
    if not condition:
        raise ValueError(message)


def arguments(cmds, text, hours):
    survey = cmds.add_parser("survey", help="start or resume a milestone survey")
    survey.add_argument("--milestone", type=text, required=True)
    survey.add_argument("--criterion", type=text, required=True)
    survey.add_argument("--reuse", type=int, nargs="+", default=[])
    survey.add_argument("--role", type=text, action="append")
    survey.add_argument("--hours", type=hours, default=0.1)
    cmds.add_parser("pulse", help="show dispatchable work and the owner's outstanding decision")
    prop = cmds.add_parser("proposal", help="group evidence into one project decision")
    prop.add_argument("--question", type=text, required=True)
    prop.add_argument("--source", type=int, nargs="+", required=True)
    prop.add_argument("--options", type=Path, required=True, help="array of outcome options, or - for stdin")
    prop.add_argument("--recommend", type=text, required=True)
    decision = cmds.add_parser("decide")
    decision.add_argument("id", type=int)
    choice = decision.add_mutually_exclusive_group(required=True)
    choice.add_argument("--select", type=text)
    choice.add_argument("--defer", action="store_true")
    choice.add_argument("--decline", action="store_true")
    decision.add_argument("--authority", type=text, required=True, help="user decision and message reference")
    cmds.add_parser("survey-close").add_argument("evidence", type=text)


def enabled(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='surveys' AND type='table'").fetchone())


def current(db):
    return db.execute("SELECT * FROM surveys WHERE closure IS NULL").fetchone() if enabled(db) else None


def scan_ids(db):
    if not enabled(db):
        return set()
    # Keep the original resource locks for work already issued before adoption.
    return {r[0] for r in db.execute("""SELECT task FROM survey_cards s JOIN tasks t ON t.id=s.task
        WHERE s.prior_lease IS NULL OR t.ticket IS NOT s.prior_lease OR t.state NOT IN ('reserved','working')""")}


def start(db, project, a, save_card):
    for sql in SCHEMA:
        db.execute(sql)
    survey = db.execute("SELECT * FROM surveys WHERE milestone=?", (a.milestone,)).fetchone()
    if survey:
        check(survey["closure"] is None and survey["criterion"] == a.criterion,
              "milestone closed or criterion differs; inspect pulse")
        sid = survey["id"]
    else:
        check(not current(db), "finish the active milestone first")
        sid = db.execute("INSERT INTO surveys(milestone,criterion) VALUES(?,?)",
                         (a.milestone, a.criterion)).lastrowid
    domains = dict(db.execute("SELECT name,ownership FROM domains ORDER BY name"))
    roles = list(dict.fromkeys(a.role or domains))
    check(set(roles) <= domains.keys(), "unknown survey domain")
    scans = dict(db.execute("SELECT role,task FROM survey_cards WHERE survey=?", (sid,)))
    for tid in a.reuse:
        row = db.execute("SELECT role,state,ticket FROM tasks WHERE id=?", (tid,)).fetchone()
        check(row and row[0] in roles and scans.get(row[0], tid) == tid, "conflicting or unknown survey card")
        if row[0] not in scans:
            prior = row["ticket"] if row["state"] in ("reserved","working") else None
            db.execute("INSERT INTO survey_cards VALUES(?,?,?,?)", (sid, row[0], tid, prior))
            scans[row[0]] = tid
    for role in roles:
        if role in scans:
            continue
        scope = (f"{domains[role]} Check sources against the milestone criterion. "
                 "Work read-only and save a separate report for this card.")
        criterion = (f"Milestone criterion: {a.criterion}. Report progress, blockers, evidence, and options "
                     "with user impact. If complete, cite the evidence.")
        args = argparse.Namespace(role=role, title=f"{a.milestone} · {role} review", scope=scope,
                                  criterion=criterion, hours=a.hours, tier="simple", after=[])
        tid = save_card(db, project, args)["id"]
        db.execute("INSERT INTO survey_cards VALUES(?,?,?,NULL)", (sid, role, tid))
        scans[role] = tid
    return {"survey": sid, "cards": scans}


def options_from(db, file):
    raw = sys.stdin.read() if str(file) == "-" else file.read_text(encoding="utf-8-sig")
    options = json.loads(raw)
    check(isinstance(options, list) and 2 <= len(options) <= 3, "provide two or three distinct outcome options")
    names = set()
    roles = {r[0] for r in db.execute("SELECT name FROM domains")}
    for option in options:
        check(isinstance(option, dict) and set(option) <= {"name","outcome","tradeoff","success","work"}
              and all(isinstance(option.get(k), str) and option[k].strip()
              for k in ("name", "outcome", "tradeoff", "success")), "option needs name, outcome, tradeoff, success")
        name = option["name"].strip().casefold()
        check(name not in names, "option names must be distinct")
        names.add(name)
        work = option.get("work")
        check(isinstance(work, list), "work must be a list (empty to keep the current plan)")
        for card in work:
            check(isinstance(card, dict) and set(card) <= {"role","title","scope","criterion","hours","tier","resources"}
                  and all(isinstance(card.get(k), str) and card[k].strip()
                  for k in ("title", "role", "scope", "criterion")) and card["role"] in roles, "invalid work card")
            hours = card.get("hours")
            check(type(hours) in (float,int) and math.isfinite(hours) and hours > 0, "invalid planned hours")
            check(card.get("tier","simple") in ("routine","simple","strong"), "unknown tier")
            keys = card.get("resources", [])
            check(isinstance(keys, list) and all(isinstance(v,str) and v.strip() for v in keys), "invalid resources")
    return options


def propose(db, survey, a):
    options = options_from(db, a.options)
    check(a.recommend in [o["name"] for o in options], "recommend one of the offered options")
    for tid in set(a.source):
        source = db.execute("""SELECT t.state,t.evidence FROM survey_cards s JOIN tasks t ON t.id=s.task
                               WHERE s.survey=? AND t.id=?""", (survey["id"],tid)).fetchone()
        check(source and source["state"] == "accepted" and source["evidence"], "use checked reports as evidence")
    sources = json.dumps(sorted(set(a.source)))
    content = json.dumps(options, ensure_ascii=False, sort_keys=True)
    old = db.execute("SELECT * FROM proposals WHERE survey=? AND question=?", (survey["id"],a.question)).fetchone()
    if old:
        check((old["sources"],old["options"],old["recommended"]) == (sources,content,a.recommend),
              "existing question has different terms")
        return {"id": old["id"], "state": old["state"]}
    check(not db.execute("SELECT 1 FROM proposals WHERE survey=? AND state='pending'", (survey["id"],)).fetchone(),
          "one owner decision is already pending")
    pid = db.execute("INSERT INTO proposals(survey,question,sources,options,recommended) VALUES(?,?,?,?,?)",
                     (survey["id"],a.question,sources,content,a.recommend)).lastrowid
    return {"id": pid, "state": "pending"}


def decide(db, project, survey, a, save_card):
    item = db.execute("SELECT * FROM proposals WHERE id=? AND survey=?", (a.id,survey["id"])).fetchone()
    check(item, "unknown proposal")
    state = "selected" if a.select else ("deferred" if a.defer else "declined")
    if item["state"] == state and item["selection"] == a.select:
        linked = db.execute("SELECT task FROM approved_work WHERE proposal=?", (a.id,))
        return {"state": state, "tasks": [r[0] for r in linked]}
    check(item["state"] in ("pending","deferred"), "this proposal already has a final decision")
    tasks = []
    if a.select:
        option = next((o for o in json.loads(item["options"]) if o["name"] == a.select), None)
        check(option, "select one of the published options")
        for card in option["work"]:
            args = argparse.Namespace(role=card["role"], title=card["title"], scope=card["scope"],
                                      criterion=card["criterion"], hours=card["hours"], tier=card.get("tier","simple"),
                                      resource=card.get("resources"), after=[])
            tid = save_card(db, project, args)["id"]
            db.execute("INSERT INTO approved_work VALUES(?,?)", (tid,a.id))
            tasks.append(tid)
    db.execute("UPDATE proposals SET state=?,selection=?,authority=? WHERE id=?", (state,a.select,a.authority,a.id))
    return {"state": state, "tasks": tasks}


def overlaps(occupied, keys):
    return bool(occupied and keys and ("*" in occupied or "*" in keys or not occupied.isdisjoint(keys)))


def offers(project, ready, occupied, scans, resources_by_task):
    available = project["slots"] - sum(c["id"] not in scans for c in occupied)
    used_roles = set()
    resources = set()
    dispatch = []
    for c in sorted(ready, key=lambda c: c["id"] in scans):
        reading = c["id"] in scans
        if c["role"] in used_roles or (not reading and available <= 0):
            continue
        if resources_by_task is not None and not reading:
            keys = resources_by_task.get(c["id"]) or {"*"}
            if overlaps(resources, keys):
                continue
            resources.update(keys)
        used_roles.add(c["role"])
        dispatch.append({"id": c["id"], "role": c["role"], "kind": "analysis" if reading else "implementation"})
        if not reading:
            available -= 1
    return dispatch


def pulse(db, project, plan):
    survey = current(db)
    if not survey:
        return {"active": False}
    review = plan["review_ready"]
    item = db.execute("SELECT * FROM proposals WHERE survey=? AND state='pending'", (survey["id"],)).fetchone()
    choice = None
    if item:
        options = [{k:o[k] for k in ("name","outcome","tradeoff","success")} for o in json.loads(item["options"])]
        choice = {"id": item["id"], "question": item["question"], "options": options, "recommend": item["recommended"]}
    reports = dict(db.execute("""SELECT t.id,t.state FROM survey_cards s JOIN tasks t ON t.id=s.task
                                 WHERE s.survey=? ORDER BY t.id""", (survey["id"],)))
    return {"active": True, "milestone": survey["milestone"], "criterion": survey["criterion"],
            "paused": bool(project["paused"]), "dispatch": plan["dispatch"],
            "review": review[0] if review else None, "review_ready": review, "owner_decision": choice, "reports": reports}


def close(db, survey, evidence):
    sid = survey["id"]
    pending = db.execute("""SELECT task FROM survey_cards WHERE survey=?
        UNION SELECT w.task FROM approved_work w JOIN proposals p ON w.proposal=p.id WHERE p.survey=?""", (sid,sid))
    states = dict(db.execute("SELECT id,state FROM tasks"))
    check(all(states[tid] == "accepted" for (tid,) in pending), "outstanding surveys or approved work")
    check(not db.execute("SELECT 1 FROM proposals WHERE survey=? AND state='pending'", (sid,)).fetchone(),
          "owner decision pending")
    db.execute("UPDATE surveys SET closure=? WHERE id=?", (evidence,sid))
    return {"survey": sid, "closed": True}
