"""Exercise real CLI transactions, a live subprocess and isolated Codex telemetry."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import time

from verify_team import add, cli, init


def save(path, value):
    path.write_text(json.dumps(value), encoding="utf8")
    return path


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def window(root):
    init(root, "--slots", 4, "--git", "none")
    roles = ("Draft", "Notes", "Figures", "Index")
    cli(root, "lead", "organize", save(root / "roles.json", dict(zip(roles, roles))), "--slots", 4)

    def submitted(role, resource, *, short=False):
        task = add(root, role, "Check " + role, "--resource", resource)
        key = cli(root, "lead", "dispatch", task)["brief"]["ticket"]
        cli(root, role, "claim", task, key)
        proof = root / (role + ".txt")
        proof.write_text(role, encoding="utf8")
        cli(
            root,
            role,
            "submit",
            task,
            key,
            "Checked against source",
            "--artifact",
            proof.name,
            "--revision",
            digest(proof),
            *(["--quick-review"] if short else []),
        )
        return task, digest(proof)

    main, version = submitted("Draft", "shared")
    quick, other_version = submitted("Notes", "notes", short=True)
    overlap, _ = submitted("Figures", "shared", short=True)
    ordinary, _ = submitted("Index", "index")
    job = cli(root, "lead", "review", main)["brief"]
    jid, key = job["job"], job["ticket"]
    cli(root, "reviewer", "job-claim", jid, key)

    def test(*args, actor="reviewer", fail=False):
        return cli(root, actor, "review-test", jid, key, main, "--", sys.executable, "-c", *args, fail=fail)

    def decide(task, ref, decision):
        return cli(root, "reviewer", "verdict", jid, key, decision, "verified", "--task", task, "--revision", ref)

    signal = root / "finished"

    def watch(*args):
        return cli(root, "lead", "inbox", "--watch", "--parts", "--card", main, *args)

    with ThreadPoolExecutor(max_workers=2) as pool:
        task = pool.submit(
            test,
            "from pathlib import Path\nimport time\nfor _ in range(300):\n if Path('finished').is_file(): break\n time.sleep(.04)\nelse: raise SystemExit(3)",
        )
        try:
            until = time.monotonic() + 8
            while time.monotonic() < until:
                info = watch()
                service = info["board"]["service_jobs"][0]
                if service.get("test"):
                    break
                time.sleep(0.05)
            assert service["short_ready"] == [quick] and service["test"]["status"] == "running"
            assert info["board"]["watch"]["recheck_at"]
            assert "reviewer ticket" in test("pass", actor="Notes", fail=True)
            cli(root, "lead", "job-cancel", jid, "pending", fail=True)
            cli(
                root,
                "reviewer",
                "verdict",
                jid,
                key,
                "approve",
                "early",
                "--task",
                main,
                "--revision",
                version,
                fail=True,
            )
            for wrong in (overlap, ordinary, main):
                cli(root, "lead", "review-side", jid, wrong, fail=True)
            results = [pool.submit(cli, root, "lead", "review-side", jid, quick) for _ in range(2)]
            outcomes = []
            for result in results:
                try:
                    outcomes.append(result.result())
                except AssertionError as error:
                    assert "independent short review" in str(error)
            assert (
                len(outcomes) == 1
                and outcomes[0]["threadId"] == "reviewer"
                and outcomes[0]["brief"]["already_claimed"]
            )
            assert decide(quick, other_version, "changes")["state"] == "active"
            state = watch()
            asleep = pool.submit(watch, "--after", state["cursor"], "--wait", 5)
            signal.touch()
            assert task.result()["exit_code"] == 0
            assert asleep.result()["board"]["service_jobs"][0]["test"]["status"] == "passed"
        finally:
            signal.touch()
    test("raise SystemExit(2)", fail=True)
    assert cli(root, "reviewer", "job", jid)["test"]["status"] == "failed"
    cli(
        root,
        "reviewer",
        "verdict",
        jid,
        key,
        "approve",
        "early",
        "--task",
        main,
        "--revision",
        version,
        fail=True,
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        lost = pool.submit(
            cli,
            root,
            "reviewer",
            "review-test",
            "--recheck",
            ".5",
            jid,
            key,
            main,
            "--",
            sys.executable,
            "-c",
            "from pathlib import Path\nimport time\nwhile not Path('finish-old').exists(): time.sleep(.02)",
            fail=True,
        )
        try:
            until = time.monotonic() + 6
            while time.monotonic() < until:
                service = watch()["board"]["service_jobs"][0]
                if service.get("test", {}).get("status") == "check":
                    break
                time.sleep(0.05)
            overdue = service["test"]
            assert overdue["status"] == "check" and "short_ready" not in service
            cli(root, "lead", "review-interrupt", jid, key, "wrong", "reconciled", fail=True)
            cli(root, "lead", "review-interrupt", jid, key, overdue["run"], "test process reconciled")
            test("pass")  # a late callback for the old run must not replace this success
        finally:
            (root / "finish-old").touch()
        assert "reconciled" in lost.result()
        assert cli(root, "reviewer", "job", jid)["test"]["status"] == "passed"
    assert decide(main, version, "approve")["state"] == "mixed"
    return jid, main, quick


def telemetry(home, thread, model, cases, duration, tokens):
    logfile = home / (thread + ".jsonl")
    rows = []
    for i, c in enumerate(cases):
        tid = c["case"]
        start = 100 + i * 50
        rows.extend(
            [
                {"type": "event_msg", "payload": {"type": "task_started", "turn_id": tid, "started_at": start}},
                {"type": "turn_context", "payload": {"turn_id": tid, "model": model, "effort": "low"}},
                {
                    "type": "response_item",
                    "payload": {"role": "user", "content": [{"text": f"eval:{tid}:{c['sha256']}"}]},
                },
                {
                    "type": "token_usage_record",
                    "payload": {
                        "response_id": tid,
                        "thread_id": thread,
                        "turn_id": tid,
                        "usage": {"input_tokens": tokens, "cached_input_tokens": 0, "output_tokens": 2},
                    },
                },
                {
                    "type": "event_msg",
                    "payload": {"type": "task_complete", "turn_id": tid, "completed_at": start + duration},
                },
            ]
        )
    logfile.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf8")
    with closing(sqlite3.connect(home / "state_5.sqlite")) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS threads(id TEXT PRIMARY KEY,rollout_path TEXT)")
        db.execute("INSERT OR REPLACE INTO threads VALUES(?,?)", (thread, str(logfile)))


def routing(root, job, approved, returned):
    home = root.parent / "codex"
    (root / ".codex").mkdir(exist_ok=True)
    save(root / ".codex/harness-models.json", {"routine": "quick-model"})
    work_type = "reconcile"
    task = add(root, "Figures", "Reconcile records", "--tier", "routine", "--resource", "figures")
    default = cli(root, "lead", "dispatch", task, "--work-type", work_type)
    assert default["model"] == "fast-local"
    cli(root, "Figures", "claim", task, default["brief"]["ticket"])
    assert cli(root, "lead", "next-stage", task, "--stage", "waiting")["operation"] == "wait"
    assert cli(root, "lead", "next-stage", task, "--stage", "diagnosis")["model"] == "capable-local"
    cli(root, "Figures", "next-stage", task, "--stage", "bounded", fail=True)
    cases = save(
        root / "inputs.json",
        [
            {"job": job, "task": approved, "input": "Draft.txt"},
            {"job": job, "task": returned, "input": "Notes.txt"},
        ],
    )

    def trial(fault=None):
        pack = cli(root, "lead", "model-pack", work_type, cases)
        assert cli(root, "lead", "model-pack", work_type, cases) == pack
        choices = pack["cases"]
        assert all(set(case) == {"input", "case", "sha256"} for case in choices)
        arms = {}
        for model in pack["models"]:
            fast = model == "quick-model"
            thread = model + str(pack["batch"])
            telemetry(
                home,
                thread,
                model,
                choices,
                (7 if fault == "slow" else 3) if fast else 5,
                (70 if fault == "waste" else 50) if fast else 60,
            )
            arm = arms[model] = {}
            for case in choices:
                ref = case["case"]
                output = root / (thread + ref + ".txt")
                output.write_text("comparison for " + ref)
                actual = "approve" if case["input"] == "Draft.txt" else "changes"
                arm[ref] = {
                    "decision": "approve" if fast and fault == "miss" else actual,
                    "quality_pass": True,
                    "input_sha256": case["sha256"],
                    "output": output.name,
                    "output_sha256": digest(output),
                    "thread": thread,
                    "first_turn": ref,
                    "last_turn": ref,
                }
        score = save(root / "scores.json", arms)
        cli(root, "lead", "model-score", pack["batch"], score, fail=True)
        original = next(iter(arms["quick-model"].values()))
        good_thread = original["thread"]
        original["thread"] = next(iter(arms["fast-local"].values()))["thread"]
        cli(root, "reviewer", "model-score", pack["batch"], save(score, arms), fail=True)
        original["thread"] = good_thread
        correct = original["output_sha256"]
        original["output_sha256"] = "wrong"
        cli(root, "reviewer", "model-score", pack["batch"], save(score, arms), fail=True)
        original["output_sha256"] = correct
        return cli(root, "reviewer", "model-score", pack["batch"], save(score, arms))

    assert trial("miss")["state"] == "rejected"
    assert trial("slow")["state"] == "rejected"
    assert trial("waste")["state"] == "rejected"
    assert cli(root, "lead", "next-stage", task, "--stage", "bounded")["model"] == "fast-local"
    assert trial()["state"] == "approved"
    assert cli(root, "lead", "next-stage", task, "--stage", "bounded")["model"] == "quick-model"
    assert (
        cli(root, "lead", "next-stage", task, "--stage", "bounded", "--work-type", "untested")["model"]
        == "fast-local"
    )
    assert cli(root, "lead", "next-stage", task, "--stage", "diagnosis")["model"] == "capable-local"
    proof = root / "Figures.txt"
    cli(
        root,
        "Figures",
        "submit",
        task,
        default["brief"]["ticket"],
        "ready",
        "--revision",
        digest(proof),
        "--artifact",
        proof.name,
    )
    j = cli(root, "lead", "review", task)["brief"]
    cli(root, "reviewer", "job-claim", j["job"], j["ticket"])
    cli(root, "reviewer", "verdict", j["job"], j["ticket"], "changes", "regression", "--revision", digest(proof))
    assert cli(root, "lead", "model-status")[0]["state"] == "revoked"
    fresh = add(root, "Figures", "Another case", "--resource", "another")
    assert (
        cli(root, "lead", "dispatch", fresh, "--stage", "bounded", "--work-type", work_type)["model"]
        == "fast-local"
    )


if __name__ == "__main__":
    with TemporaryDirectory() as temp:
        root = Path(temp) / "reports"
        root.mkdir()
        routing(root, *window(root))
    print(
        "OK: same-reviewer background handoff, exact verdicts, blind paired telemetry, escalation and revocation"
    )
