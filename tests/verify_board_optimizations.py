"""Regression checks for conditional board reads, resource locks and concurrent usage reports."""

from contextlib import closing
import json
import os
from pathlib import Path
import runpy
import sqlite3
from tempfile import TemporaryDirectory
import time
from unittest.mock import patch

from verify_team import SCRIPTS, add, cli, indexed_log, init


def part_updates(root):
    init(root)
    source = add(root, "Data", "Compare the source rows")
    task = add(root, "Editing", "Edit the release note", "--after", source)

    def read(*args, actor="lead", watched=task):
        return cli(root, actor, "inbox", "--parts", "--card", watched, *args)

    first = read()
    assert first["card"] == cli(root, "lead", "show", task)
    assert first["board"]["counts"] == {"open": 2}
    assert read("--after", first["cursor"], "--wait", .01) == {"changed": False}
    add(root, "Layout", "Check headings")
    board = read("--after", first["cursor"])
    assert set(board) == {"board", "cursor", "changed"} and board["board"]["counts"] == {"open": 3}
    cli(root, "lead", "edit", task, "--criterion", "All headings match the source")
    card = read("--after", board["cursor"])
    assert set(card) == {"card", "cursor", "changed"} and card["card"]["criterion"].startswith("All headings")
    assert read()["board"] == board["board"] and read()["card"] == card["card"]
    brief = cli(root, "lead", "dispatch", source)["brief"]
    result = read("--after", card["cursor"])
    assert result["card"]["prerequisites"][0]["state"] == "reserved" and result["board"]["workers"]
    assert read("--after", result["cursor"]) == {"changed": False}
    lead_card = read(watched=source)
    worker_card = read("--after", lead_card["cursor"], watched=source, actor="reader")
    assert worker_card["board"] and "ticket" not in worker_card["card"]
    for reset in (read("--after", lead_card["cursor"]), read("--watch", "--after", result["cursor"]),
                  read("--after", "legacy-cursor"), read()):
        assert {"board", "card", "cursor"} <= reset.keys()
    cli(root, "reader", "claim", source, brief["ticket"])
    cli(root, "reader", "submit", source, brief["ticket"], "Verified source rows")
    final = read("--after", result["cursor"])
    assert final["card"]["prerequisites"][0]["evidence"] == "Verified source rows"
    assert "requires --card" in cli(root,"lead","inbox","--parts",fail=True)
    return final["cursor"]


def check_locks(root, board_path):
    init(root, "--slots", 5)
    owners = {role: role for role in ("Write", "Other", "Free", "Check", "Legacy")}
    manifest = root / "owners.json"
    manifest.write_text(json.dumps(owners))
    cli(root, "lead", "organize", manifest, "--slots", 5, "--git", "none")
    busy = add(root,"Write","Update the index","--resource","shared")
    blocked = add(root,"Other","Edit the index","--resource","shared")
    free = add(root,"Free","Update labels","--resource","labels")
    everyone = add(root,"Check","Review all files","--resource","*")
    legacy = add(root,"Legacy","Prepare appendix")
    cli(root,"lead","dispatch",busy)
    with closing(sqlite3.connect(board_path(root))) as db, db:
        db.execute("DELETE FROM resources WHERE task=?", (legacy,))

    def ready():
        return [x["id"] for x in cli(root, "lead", "plan", "--ready")["tasks"]]

    assert ready() == [free, legacy]
    assert [x["id"] for x in cli(root,"lead","inbox")["dispatch"]] == [free]
    for task in (blocked, everyone):
        assert "scope occupied" in cli(root,"lead","dispatch",task,fail=True)
    with closing(sqlite3.connect(board_path(root))) as db, db:
        db.execute("UPDATE resources SET name='*' WHERE task=?", (busy,))
    assert ready() == [legacy]
    cli(root,"lead","dispatch",legacy)


def unlocked_usage(root, board_path):
    init(root)
    add(root, "Research", "Validate figures")
    home = root.parent / "codex"
    indexed_log(home, "lead", int(time.time()), (40,10,5), 500)
    path = board_path(root)
    measure = runpy.run_path(str(SCRIPTS/"team_usage.py"))["measure"]
    discover = measure.__globals__["rollout_paths"]
    def concurrent_write(home, threads):
        # This independent writer must commit before we even open the rollout index.
        add(root, "Editing", "Check the captions")
        return discover(home, threads)
    with patch.dict(measure.__globals__, rollout_paths=concurrent_write):
        snapshot = measure(path)
    assert [r["role"] for r in snapshot["by_role"]] == ["Research"]
    assert snapshot["measured"]["input"] == 40 and snapshot["measured"]["cached_input"] == 10
    fresh = cli(root, "lead", "usage")
    assert len(fresh["by_role"]) == 2 and fresh["measured"] == snapshot["measured"]


def survey_locks(root):
    init(root, "--slots", 3)
    manifest = root / "owners.json"
    manifest.write_text(json.dumps({name: name for name in ("Data", "Layout", "Editing")}))
    cli(root, "lead", "organize", manifest, "--slots", 3, "--git", "none")
    existing = add(root, "Data", "Update the source", "--resource", "shared")
    edit = add(root, "Editing", "Edit the export", "--resource", "shared")
    cli(root, "lead", "dispatch", existing)
    survey = cli(root, "lead", "survey", "--milestone", "Publish the catalogue",
                 "--criterion", "All roles report", "--role", "Data", "--role", "Layout", "--reuse", existing)
    reader = survey["cards"]["Layout"]
    assert cli(root, "lead", "dispatch", reader)["brief"]["analysis_only"]
    assert edit not in [c["id"] for c in cli(root, "lead", "plan", "--ready")["tasks"]]
    assert "scope occupied" in cli(root, "lead", "dispatch", edit, fail=True)
    cli(root, "lead", "release", existing, "Source update postponed")
    assert edit in [c["id"] for c in cli(root, "lead", "pulse")["dispatch"]]
    cli(root, "lead", "dispatch", edit)


if __name__ == "__main__":
    with TemporaryDirectory() as folder:
        base = Path(folder)
        with patch.dict(os.environ, CODEX_HOME=str(base/"codex")):
            board_path = runpy.run_path(str(SCRIPTS/"harness.py"))["team_board"]
            for name in ("catalogue", "catalogue copy", "locks", "report", "survey"):
                (base/name).mkdir()
            cursor = part_updates(base/"catalogue")
            part_updates(base/"catalogue copy")
            other = cli(base/"catalogue copy", "lead", "inbox", "--card", 2, "--parts", "--after", cursor)
            assert {"board", "card"} <= other.keys()
            check_locks(base/"locks", board_path)
            survey_locks(base/"survey")
            unlocked_usage(base/"report", board_path)
    print("OK: reconstructible snapshots, isolated cursors, exact resource locks and writable board during usage")
