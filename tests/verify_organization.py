"""Run two independent owners, a reviewer and a publisher using normal CLI commands."""

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
from tempfile import TemporaryDirectory

from verify_team import add, cli, init


def ticket(root, actor, assignment):
    brief = assignment["brief"]
    claimed = cli(root, actor, "job-claim", brief["job"], brief["ticket"])
    assert claimed["on_blocker"] == brief["on_blocker"] and claimed["on_blocker"]
    return [brief["job"], brief["ticket"]]


def scenario(root):
    root.mkdir()
    roster = root / "owners.json"
    roster.write_text(json.dumps({"Research": "Source materials", "Editing": "Final copy", "Design": "Layout"}))
    # Save first-use Git choice before team setup. Neither action initializes Git.
    assert cli(root, "lead", "git-choice") is None
    cli(root, "lead", "git-choice", "--git", "none")
    assert cli(root, "lead", "intake")["stage"] == "new"
    assert cli(root, "lead", "intake")["git"]["mode"] == "none"
    assert "no team board" in cli(root, "lead", "plan", fail=True)
    init(root)
    assert cli(root, "lead", "organize", roster, "--slots", 2)["slots"] == 2
    assert cli(root, "lead", "intake")["organization"]
    assert not (root / ".git").exists()
    def card(role, resource):
        return cli(root, "lead", "add", "--title", "Complete " + role, "--role", role, "--scope", resource,
                   "--criterion", "Compare output with the source", "--hours", 0.25, "--resource", resource)["id"]
    a, b, c = card("Research", "totals"), card("Editing", "copy"), card("Design", "Totals")
    empty = cli(root, "lead", "inbox", "--reviews")
    assert not empty["reviews"]
    assert cli(root, "lead", "inbox", "--next")["action"] == "dispatch"
    brief = cli(root, "lead", "dispatch", a)["brief"]
    first = brief["ticket"]
    assert "scope occupied" in cli(root, "lead", "dispatch", c, fail=True)
    second = cli(root, "lead", "dispatch", b)["brief"]["ticket"]
    claimed = cli(root, "A", "claim", a, first)
    assert claimed["on_blocker"] == brief["on_blocker"] and claimed["on_blocker"]
    # The assignee remains observable via Desktop even when it ends its turn before submission.
    current = cli(root, "lead", "inbox", "--watch", "--card", a, "--parts")
    assert current["card"]["state"] == "working"
    assert "A" in {t["threadId"] for t in current["board"]["watch"]["targets"]}
    cli(root, "B", "claim", b, second)
    step = ["--next", "--prep", "check fixture format"]
    assert cli(root, "lead", "inbox", *step)["step"] == step[-1]
    assert cli(root, "lead", "inbox", "--next")["action"] == "wait"
    assert cli(root, "lead", "inbox", "--reviews", "--after", empty["cursor"]) == {"changed": False}
    assert not cli(root, "lead", "plan", "--ready")["tasks"]
    path = root / "final.txt"
    path.write_bytes(b"protected result")
    ref = hashlib.sha256(path.read_bytes()).hexdigest()
    def submit(assignment, ref):
        cli(root, "A", "submit", a, assignment, "compared to source", "--revision", ref, "--artifact", path.name)
    cli(root, "A", "submit", a, first, "evidence", fail=True)
    submit(first, ref)
    event = cli(root, "lead", "inbox", "--reviews", "--after", empty["cursor"])
    assert event["reviews"] == [{"id": a, "revision": ref}]
    assert cli(root, "lead", "inbox", "--reviews", "--wait", 0.1)["reviews"] == event["reviews"]
    assert cli(root, "lead", "inbox", *step)["action"] == "review"
    assert "changed revision" in cli(root, "lead", "review", a, "--expect-revision", "1"*64, fail=True)
    cli(root, "lead", "accept", a, "self approval", fail=True)
    review = cli(root, "lead", "review", a, "--expect-revision", ref)
    assert review["brief"]["on_idle"] and not cli(root, "lead", "inbox", "--reviews")["reviews"]
    assert "already assigned" in cli(root, "lead", "review", a, "--expect-revision", ref, fail=True)
    assert review["model"] == "capable-local" and review["operation"] == "create_thread"
    jid, key = review["brief"]["job"], review["brief"]["ticket"]
    for actor in ("A", "B", "lead"):
        cli(root, actor, "job-claim", jid, key, fail=True)
    args = ticket(root, "reviewer", review)
    assert cli(root, "reviewer", "intake")["actor"] == "review"
    assert cli(root, "", "intake")["actor"] == "missing"
    assert "current project lead" in cli(root, "visitor", "handoff", "visitor", fail=True)
    for bound in ("A", "reviewer"):
        assert "already owns" in cli(root, "lead", "handoff", bound, fail=True)
    before = cli(root, "lead", "plan")
    transfer = cli(root, "lead", "handoff", "successor")
    assert cli(root, "lead", "handoff", "successor") == transfer  # lost reply, no second mutation
    assert cli(root, "successor", "intake")["actor"] == "lead"
    assert cli(root, "lead", "intake")["actor"] == "other"
    assert "project lead" in cli(root, "lead", "inbox", "--watch", fail=True)
    assert cli(root, "successor", "plan") == before
    assert cli(root, "successor", "show", b)["state"] == "working"
    assert "independent reviewer" in cli(root, "successor", "accept", a, "approve", fail=True)
    cli(root, "successor", "verdict", *args, "approve", "wrong actor", "--revision", ref, fail=True)
    path.write_bytes(b"changed")
    cli(root, "reviewer", "verdict", *args, "approve", "wrong bytes", "--revision", ref, fail=True)
    cli(root, "reviewer", "verdict", *args, "changes", "restore the agreed text", "--revision", ref)
    cli(root, "successor", "handoff", "lead")
    assert cli(root, "lead", "show", a)["returns"] == 1
    retry = cli(root, "lead", "dispatch", a)
    assert retry["threadId"] == "A"
    key = retry["brief"]["ticket"]
    cli(root, "A", "claim", a, key)
    path.write_bytes(b"accepted text")
    ref = hashlib.sha256(path.read_bytes()).hexdigest()
    submit(key, ref)
    review = cli(root, "lead", "review", a)
    assert review["threadId"] == "reviewer"
    args = ticket(root, "reviewer", review)
    cli(root, "A", "verdict", *args, "approve", "author", "--revision", ref, fail=True)
    cli(root, "reviewer", "verdict", *args, "approve", "compared actual file", "--revision", ref)
    req = ["--target", "approved folder", "--revision", ref]
    cli(root, "lead", "delivery", a, b, *req, "--action", "prepare", fail=True)
    cli(root, "lead", "delivery", a, *req, "--action", "publish", fail=True)
    cli(root, "lead", "delivery", a, *req, "--action", "prepare", "--effects", "git", fail=True)
    prep = cli(root, "lead", "delivery", a, *req, "--action", "prepare")
    assert prep["model"] == "fast-local"
    cli(root, "lead", "delivery", a, *req, "--action", "prepare", fail=True)
    cli(root, "reviewer", "job-claim", prep["brief"]["job"], prep["brief"]["ticket"], fail=True)
    path.write_bytes(b"stale package")
    cli(root, "publisher", "job-claim", prep["brief"]["job"], prep["brief"]["ticket"], fail=True)
    path.write_bytes(b"accepted text")
    args = ticket(root, "publisher", prep)
    cli(root, "publisher", "delivered", *args, "prepared", "--revision", ref)
    release = cli(root, "lead", "delivery", a, *req, "--action", "publish", "--authority", "test owner instruction")
    assert release["threadId"] == "publisher" and release["brief"]["git"]["mode"] == "none"
    args = ticket(root, "publisher", release)
    dest = root / "approved"
    dest.mkdir()
    (dest / path.name).write_bytes(path.read_bytes())
    assert hashlib.sha256((dest / path.name).read_bytes()).hexdigest() == ref
    cli(root, "publisher", "delivered", *args, "destination SHA-256 verified", "--revision", ref)
    cli(root, "lead", "delivery", a, *req, "--action", "publish", "--authority", "retry", fail=True)
    assert not (root / ".git").exists()
    assert not cli(root, "lead", "plan")["service_jobs"]
    usage = cli(root, "lead", "usage")
    assert set(usage["missing_chats"]) == {"lead","successor","A","B","reviewer","publisher"}
    assert usage["accepted_tasks"] == 1


def existing_git(root):
    root.mkdir()
    assert "existing Git" in cli(root, "lead", "git-choice", "--git", "existing", fail=True)
    checkout = root / "sources"
    subprocess.run(["git","init","-q",str(checkout)], check=True)
    subprocess.run(["git","-C",str(checkout),"-c","user.name=Fixture","-c","user.email=fixture@example.org",
                    "commit","-qm","Initial","--allow-empty"],check=True)
    cli(root, "lead", "git-choice", "--git","existing","--git-dir","sources")
    init(root)
    roster = root / "owners.json"
    roster.write_text('{"Code":"Source code"}')
    cli(root,"lead","organize",roster,"--slots",2)
    cli(root,"lead","add","--role","Code","--scope","sources","--title","commit","--hours",1,"--criterion","exact commit")
    key = cli(root,"lead","dispatch",1)["brief"]["ticket"]
    cli(root,"coder","claim",1,key)
    ref = subprocess.check_output(["git","-C",str(checkout),"rev-parse","HEAD"],text=True).strip()
    cli(root,"coder","submit",1,key,"unknown commit","--revision","0"*40,fail=True)
    cli(root,"coder","submit",1,key,"known commit","--revision",ref)
    assert Path(cli(root,"lead","intake")["git"]["directory"]) == checkout.resolve()


def grouped_reviews(root):
    root.mkdir()
    init(root)
    owners = root / "owners.json"
    roles = [f"Role{i}" for i in range(11)]
    owners.write_text(json.dumps({r:r for r in roles}))
    cli(root, "lead", "organize", owners, "--slots", 2, "--git", "none")
    a, b, c = [add(root, role, role, "--resource", role) for role in roles[:3]]
    dependent = add(root, roles[3], "needs first result", "--after", a, "--resource", roles[3])
    cli(root, "lead", "survey", "--role", roles[-1], "--milestone", "Complete project", "--criterion", "Verified results")

    def dispatch(tid):
        key = cli(root, "lead", "dispatch", tid)["brief"]["ticket"]
        cli(root, f"worker{tid}", "claim", tid, key)
        return key

    def submit(tid, key, content):
        path = root / f"{tid}.txt"
        path.write_bytes(content)
        ref = hashlib.sha256(content).hexdigest()
        cli(root, f"worker{tid}", "submit", tid, key, "checked candidate", "--artifact", path.name, "--revision", ref)
        return ref

    ka, kb = dispatch(a), dispatch(b)
    rev_a = submit(a, ka, b"A")
    assert cli(root, "lead", "pulse")["review_ready"] == [a]
    rev_b = submit(b, kb, b"B")
    assert cli(root, "lead", "pulse")["review_ready"] == [a,b]
    cli(root, "lead", "review", a, c, fail=True)
    assignment = cli(root, "lead", "review", a, b)
    assert {x["id"]:x["revision"] for x in assignment["brief"]["cards"]} == {a:rev_a, b:rev_b}
    jid, key = ticket(root, "reviewer", assignment)
    assert not cli(root, "lead", "plan")["review_ready"]
    kc = dispatch(c)  # next independent work starts while review is active
    cli(root, "lead", "dispatch", dependent, fail=True)
    cli(root, "reviewer", "verdict", jid, key, "approve", "ambiguous", "--revision", rev_a, fail=True)
    rows = [dict(task=a, revision=rev_a, decision="approve", note="checked A"),
            dict(task=b, revision=rev_b, decision="changes", note="missing case in B")]
    file = root / "decisions.json"
    def decisions(values, fail=False, actor="reviewer"):
        file.write_text(json.dumps(values), encoding="utf8")
        return cli(root, actor, "verdicts", jid, key, file, fail=fail)
    decisions([rows[0], dict(rows[1],revision=rev_a)], fail=True)
    decisions([rows[0], rows[0]], fail=True)
    assert cli(root, "lead", "show", a)["state"] == "review"
    decisions(rows, fail=True, actor=f"worker{a}")
    (root / f"{a}.txt").write_bytes(b"replaced")
    decisions(rows, fail=True)
    (root / f"{a}.txt").write_bytes(b"A")
    assert decisions(rows[:1])["state"] == "active"
    dispatch(dependent)
    req = ["--target","output","--revision",rev_a,"--action","prepare"]
    delivery = cli(root, "lead", "delivery", a, *req)["brief"]
    cli(root, "lead", "job-cancel", delivery["job"], "checked that preparation has not started")
    assert decisions(rows)["state"] == "mixed"
    assert decisions(rows)["state"] == "mixed"  # retry after lost reply
    decisions([dict(rows[1], decision="approve")], fail=True)
    assert cli(root, "lead", "show", b)["returns"] == 1
    cli(root, "lead", "delivery", b, "--target","output","--revision",rev_b,"--action","prepare",fail=True)
    rev_c = submit(c, kc, b"C")
    rev_b = submit(b, dispatch(b), b"corrected B")
    again = cli(root, "lead", "review", b, c)
    jid, key = ticket(root, "reviewer", again)
    cli(root, "reviewer", "verdict", jid, key, "approve", "checked correction", "--task", b, "--revision", rev_b)
    cli(root, "lead", "job-cancel", jid, "reviewer stopped before C")
    assert cli(root, "lead", "pulse")["review_ready"] == [c]
    cli(root, "reviewer", "verdict", jid, key, "approve", "late result", "--task", c, "--revision", rev_c, fail=True)
    assert cli(root, "lead", "delivery", b, "--target","output","--revision",rev_b,"--action","prepare")["brief"]["approvals"][0]["note"] == "checked correction"
    timing = cli(root, "lead", "usage")["dispatch_to_accept"]
    assert timing["measured_cards"] == 2 and timing["median_seconds"] > 0


def legacy_reviews(root):
    root.mkdir()
    init(root)
    owners = root / "owners.json"
    owners.write_text('{"Docs":"Documentation"}')
    cli(root, "lead", "organize", owners, "--slots", 2, "--git", "none")
    path = root / "report.txt"
    path.write_bytes(b"shared revision")
    ref = hashlib.sha256(path.read_bytes()).hexdigest()
    for i in range(2):
        tid = add(root, "Docs", str(i))
        key = cli(root, "lead", "dispatch", tid)["brief"]["ticket"]
        cli(root, "writer", "claim", tid, key)
        cli(root, "writer", "submit", tid, key, "checked text", "--revision", ref, "--artifact", path.name)
        jid, key = ticket(root, "reviewer", cli(root, "lead", "review", tid))
        if i == 0:
            cli(root, "reviewer", "verdict", jid, key, "approve", "previously approved", "--revision", ref)
    # Isolated fixture now has the schema and data an older installation left on disk.
    directory = root.parent / "codex" / "loomharness" / "teams"
    board = next(directory.glob("*.sqlite3"))
    with closing(sqlite3.connect(board)) as db, db:
        db.execute("DROP TABLE review_items")
        db.execute("DROP TABLE card_timings")
    assert cli(root, "lead", "job", jid)["state"] == "active"
    cli(root, "reviewer", "verdict", jid, key, "approve", "legacy in flight", "--revision", ref)
    release = cli(root, "lead", "delivery", 1, 2, "--target","local","--action","prepare","--revision",ref)
    assert [x["note"] for x in release["brief"]["approvals"]] == ["previously approved","legacy in flight"]
    assert cli(root, "lead", "usage")["dispatch_to_accept"]["measured_cards"] == 0


if __name__ == "__main__":
    with TemporaryDirectory() as temp:
        scenario(Path(temp) / "Without Git")
        existing_git(Path(temp) / "nested")
        grouped_reviews(Path(temp) / "eleven roles")
    with TemporaryDirectory() as temp:
        legacy_reviews(Path(temp) / "upgrade")
    print("OK: eleven roles, grouped and partial verdicts, legacy reviews, exact candidates and delivery")
