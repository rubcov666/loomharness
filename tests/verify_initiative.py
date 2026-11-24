"""Exercise free-domain surveys, project-level decisions and the approval boundary."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from verify_team import cli, init


def accept(root, role, tid, brief):
    key = brief["ticket"]
    cli(root, role, "claim", tid, key)
    doc = root / f"candidate-{tid}.txt"
    doc.write_text(f"observed outcome for {role}", encoding="utf8")
    ref = hashlib.sha256(doc.read_bytes()).hexdigest()
    cli(root, role, "submit", tid, key, "compared with observed records", "--revision", ref, "--artifact", doc.name)
    job = cli(root, "lead", "review", tid)["brief"]
    cli(root, "reviewer", "job-claim", job["job"], job["ticket"])
    cli(root, "reviewer", "verdict", job["job"], job["ticket"], "approve", "verified", "--revision", ref)


def exercise(root):
    init(root)
    owners = root / "owners.json"
    owners.write_text(json.dumps({"Editing": "copy", "Data": "totals", "Design": "reader experience"}))
    cli(root, "lead", "organize", owners, "--slots", 1, "--git", "none")
    def add(role, title):
        return cli(root, "lead", "add", "--role", role, "--title", title, "--scope", "known scope",
                   "--criterion", "verified", "--hours", 0.25)["id"]
    existing = add("Data", "Existing read-only report")
    before = cli(root, "lead", "dispatch", existing)["brief"]
    write = add("Editing", "Previously authorized work")
    args = ["survey", "--milestone", "First usable release", "--criterion", "Readers can compare totals",
            "--reuse", existing]
    cli(root, "worker", *args, fail=True)
    survey = cli(root, "lead", *args)
    assert cli(root, "lead", *args) == survey and survey["cards"]["Data"] == existing
    plan = cli(root, "lead", "pulse")
    assert write not in {c["id"] for c in plan["dispatch"]}  # old write lease survives adoption
    tid = survey["cards"]["Design"]
    design = cli(root, "lead", "dispatch", tid)["brief"]
    assert design["analysis_only"] and design["role"] == "Design"
    accept(root, "Data", existing, before)
    work = cli(root, "lead", "dispatch", write)["brief"]
    assert "analysis_only" not in work
    assert "role already" in cli(root, "lead", "dispatch", survey["cards"]["Editing"], fail=True)
    accept(root, "Design", tid, design)
    accept(root, "Editing", write, work)
    edit_scan = survey["cards"]["Editing"]
    accept(root, "Editing", edit_scan, cli(root, "lead", "dispatch", edit_scan)["brief"])
    title = "Compare service coverage by region"
    card = {"role":"Data", "title":title, "scope":"coverage", "criterion":"four regions visible", "hours":0.2}
    options = [{"name":"Coverage","outcome":"Regional comparisons","tradeoff":"Later export",
                "success":"Four regions compared","work":[card]},
               {"name":"Export","outcome":"Portable report","tradeoff":"Later regional detail",
                "success":"A reader can open the report offline","work":[]}]
    opt = root / "options.json"
    opt.write_text(json.dumps(options),encoding="utf8")
    proposal = ["proposal","--question","Which outcome should the release prioritize?","--source",existing,tid,
                "--options",opt,"--recommend","Coverage"]
    pid = cli(root,"lead",*proposal)["id"]
    assert cli(root,"lead",*proposal)["id"] == pid
    pending = cli(root,"lead","pulse")["owner_decision"]
    assert cli(root,"lead","inbox")["owner_decision"] == pending
    assert pending["recommend"] == "Coverage" and "work" not in pending["options"][0]
    assert title not in [c["title"] for c in cli(root,"lead","plan")["tasks"]]
    cli(root,"lead","survey-close","premature",fail=True)
    cli(root,"Data","decide",pid,"--select","Coverage","--authority","self approval",fail=True)
    cli(root,"lead","add","--role","Data","--title","unapproved","--scope","data","--criterion","ok","--hours",0.2,fail=True)
    cli(root,"lead","pause")
    cli(root,"lead","decide",pid,"--select","Coverage","--authority","owner message",fail=True)
    cli(root,"lead","resume")
    choose = ["decide",pid,"--select","Coverage","--authority","owner selected Coverage in the project chat"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, repeat = pool.map(lambda _: cli(root,"lead",*choose),range(2))
    assert first == repeat and len(first["tasks"]) == 1
    chosen = first["tasks"][0]
    cli(root,"lead","edit",chosen,"--scope","unrelated change",fail=True)
    cli(root,"lead","survey-close","still implementing",fail=True)
    accept(root,"Data",chosen,cli(root,"lead","dispatch",chosen)["brief"])
    assert cli(root,"lead","survey-close","opened report and compared four regions")["closed"]
    assert cli(root,"lead","pulse") == {"active": False}


if __name__ == "__main__":
    with TemporaryDirectory() as temp:
        exercise(Path(temp))
    print("OK: read-only capacity, adopted work, owner-level alternatives, idempotent approval and closure")
