"""Exercise the installed CLI interfaces on isolated team boards and observation windows."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from contextlib import closing
import subprocess
import sys
from tempfile import TemporaryDirectory
import time

from verify_team import SCRIPTS, add, cli, init


def board(root):
    init(root, "--slots", 2)
    def inbox(*args):
        return cli(root, "lead", "inbox", *args)
    original = inbox()
    assert original["changed"] and original["dispatch"] == []
    assert inbox("--after", original["cursor"]) == {"changed":False}
    assert cli(root, "other", "inbox", "--after", original["cursor"])["changed"]
    assert "finite" in cli(root, "lead", "inbox", "--wait", "nan", fail=True)
    owners = root / "owners.json"
    owners.write_text('{"A":"one","B":"two","C":"three"}')
    cli(root, "lead", "organize", owners, "--slots", 2, "--git", "none")
    assert "requires --watch" in cli(root,"lead","inbox","--settled","old","0",fail=True)
    flags = cli(root, "lead", "inbox", "--watch", "--roster", fail=True)
    assert "--roster and --watch" in flags and "--settled" not in flags
    assert "CODEX_THREAD_ID" in cli(root, "", "inbox", "--watch", fail=True)
    roster = inbox("--roster")
    assert len(roster["roster"]) == 3
    assert inbox("--roster","--after",roster["cursor"]) == {"changed":False}
    assert inbox("--after",roster["cursor"])["changed"]
    a = add(root,"A","bounded edit","--tier","routine","--resource","shared")
    b = add(root,"B","other edit","--resource","shared")
    c = add(root,"C","wait for A","--resource","independent","--after",a)
    ready = inbox()
    bundled = inbox("--card", a, "--watch")
    assert bundled["card"] == cli(root, "lead", "show", a)
    assert bundled["watch"] == {"targets": [], "recheck_at": None}
    assert inbox("--card", a, "--watch", "--after", bundled["cursor"]) == {"changed":False}
    assert inbox("--card", b, "--watch", "--after", bundled["cursor"])["changed"]
    assert "unknown task" in cli(root, "lead", "inbox", "--card", 987, fail=True)
    cli(root,"lead","edit",a,"--criterion","totals agree with signed export")
    updated = inbox("--card", a, "--watch", "--after", bundled["cursor"])
    assert updated["card"]["criterion"] == "totals agree with signed export"
    assert inbox("--after", ready["cursor"]) == {"changed":False}
    assert [r["id"] for r in ready["dispatch"]] == [a]
    assert inbox("--after", ready["cursor"], "--wait", "0.02") == {"changed":False}
    # Each request opens its own SQLite connection, including the writer.
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(inbox,"--after",ready["cursor"],"--wait",8)
        time.sleep(.15)
        assignment = cli(root,"lead","dispatch",a)
        assert assignment["model"] == "fast-local"
        update = pending.result(timeout=12)
    assert update["workers"][0]["id"] == a and update["dispatch"] == []
    cli(root,"lead","release",a,"worker not started")
    (root/".codex").mkdir()
    config = root/".codex/harness-models.json"
    config.write_text("{}")
    cli(root,"lead","dispatch",a,fail=True)
    assert cli(root,"lead","show",a)["state"] == "open"
    config.write_text('{"routine":"cheap-local"}')
    assignment = cli(root,"lead","dispatch",a)
    assert assignment["model"] == "fast-local" and assignment["thinking"] == "low"  # unqualified fast model
    key = assignment["brief"]["ticket"]
    cli(root,"writer","claim",a,key)
    def watch(*opts):
        return inbox("--watch", "--card", a, *opts)
    assert "ticket" not in cli(root,"writer","inbox","--card",a)["card"]
    assert "only the project lead" in cli(root,"writer","inbox","--watch",fail=True)
    current = watch()
    target, = current["watch"]["targets"]
    assert target["threadId"] == "writer" and current["card"]["ticket"] == key
    settled = ("--settled", target["assignment"], str(time.time()-27))
    quiet = watch(*settled)
    assert quiet["watch"]["targets"] == [] and quiet["watch"]["recheck_at"] == float(settled[-1])+30
    recheck = watch(*settled, "--after", quiet["cursor"], "--wait", 8)
    assert recheck["watch"] == current["watch"]
    assert watch("--settled",target["assignment"],str(time.time()+3600))["watch"] == current["watch"]
    assert inbox("--roster","--after",roster["cursor"])["changed"]
    (root/"sample.txt").write_bytes(b"first")
    ref = hashlib.sha256(b"first").hexdigest()
    cli(root,"writer","submit",a,key,"checked","--revision",ref,"--artifact","sample.txt")
    state = inbox()
    assert state["review_ready"] == [a] and c not in [row["id"] for row in state["dispatch"]]
    rev = cli(root,"lead","review",a)
    assert rev["model"] == "capable-local"
    job = rev["brief"]
    cli(root,"reviewer","job-claim",job["job"],job["ticket"])
    assert inbox()["service_jobs"][0]["thread"] == "reviewer"
    review_target, = watch(*settled)["watch"]["targets"]
    assert review_target["threadId"] == "reviewer"
    cli(root,"reviewer","verdict",job["job"],job["ticket"],"changes","edge case","--revision",ref)
    assert cli(root,"lead","dispatch",a)["model"] == "fast-local"
    rebound, = watch("--settled", target["assignment"], str(time.time()))["watch"]["targets"]
    assert rebound["threadId"] == "writer" and rebound["assignment"] != target["assignment"]
    assert b not in [row["id"] for row in inbox()["dispatch"]]
    cli(root,"lead","pause")
    before = inbox()
    assert before["paused"] and not before["dispatch"]
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(inbox,"--after",before["cursor"],"--wait",8)
        time.sleep(.15)
        (root/"AGENT_STOP").touch()
        assert pending.result(timeout=12)["stopped"]
    assert watch()["watch"]["targets"] == []


def plain_board(root):
    root.mkdir()
    init(root, "--slots", 2)
    first = add(root, "Analysis", "Validate the quarterly data")
    second = add(root, "Editing", "Prepare the report")
    for task, actor in ((first, "analyst"), (second, "editor")):
        key = cli(root, "lead", "dispatch", task)["brief"]["ticket"]
        cli(root, actor, "claim", task, key)
    def view(*args):
        return cli(root, "lead", "inbox", "--card", first, "--watch", *args)
    baseline = view()
    targets = {item["threadId"]: item["assignment"] for item in baseline["watch"]["targets"]}
    assert targets.keys() == {"analyst", "editor"}
    assert baseline["card"] == cli(root, "lead", "show", first)
    quiet = view("--settled", targets["analyst"], time.time(), "--after", baseline["cursor"])
    assert [item["threadId"] for item in quiet["watch"]["targets"]] == ["editor"]
    assert view("--after", quiet["cursor"])["watch"] == baseline["watch"]


def metrics(root):
    one, two, three = [f"2026-01-01T0{i}:00:00Z" for i in (1,2,3)]
    data = {"windows":[{"start":two,"end":three,"expected":0},{"start":one,"end":two,"expected":1}],
            "samples":[{"at":three,"value":27},{"at":one,"value":20}]}
    def run(obj, fail=False):
        proc = subprocess.run([sys.executable,"-I",str(SCRIPTS/"metrics.py"),"-","--at-least","20"],
                              input=json.dumps(obj),text=True,capture_output=True,cwd=root,timeout=10)
        if fail:
            assert proc.returncode and "Traceback" not in proc.stderr
            return
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)
    r = run(data)
    assert r["gaps"] == [two] and r["peak"] == r["alerts"][0] == {"at":one,"value":20}
    assert r["outside_count"] == 1 and r["outside_alerts"][0]["at"] == three
    series = root / "samples.json"
    series.write_text(json.dumps(data), encoding="utf-8-sig")
    summary = json.loads(subprocess.check_output(
        [sys.executable, "-I", SCRIPTS / "metrics.py", series, "--at-least", "20", "--summary"]))
    expected = dict(r)
    expected["alert_count"] = len(expected.pop("alerts"))
    expected["outside_alert_count"] = len(expected.pop("outside_alerts"))
    assert summary == expected

    def rpc(**args):
        call = {"id": 1, "method": "tools/call", "params": {"name": "harness_metrics", "arguments": args}}
        reply = subprocess.check_output([sys.executable, "-I", "-X", "utf8", SCRIPTS / "mcp_server.py"],
                                        input=(json.dumps(call) + "\n").encode())
        return json.loads(reply)["result"]

    for args, want in (({"data": data}, r), ({"path": str(series)}, r),
                       ({"path": str(series), "root": str(root), "summary": True}, summary)):
        reply = rpc(threshold=20, **args)
        assert not reply["isError"] and json.loads(reply["content"][0]["text"]) == want
    for args in ({}, {"path": str(series), "data": data}, {"path": str(series), "root": str(root / "other")},
                 {"data": data, "summary": "false"}):
        assert rpc(**args)["isError"], args
    series.write_text('{"windows":[],"windows":[],"samples":[]}', encoding="utf8")
    assert "duplicate JSON key" in rpc(path=str(series))["content"][0]["text"]
    run(dict(data,samples=[]),fail=True)
    run(dict(data,samples=data["samples"]+[{"at":"2026-01-01T02:00:00+01:00","value":8}]),fail=True)
    run(dict(data,windows=[{"start":one,"end":three},data["windows"][0]]),fail=True)
    run(dict(data,samples=[{"at":one,"value":float("nan")}]),fail=True)
    assert run({"windows":[{"start":one,"end":two}],"samples":[]})["peak"] is None


def chat_usage(root):
    root.mkdir()
    home = root.parent / "codex"
    home.mkdir()
    start, end = "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z"
    def record(tid, key, at=start):
        return json.dumps({"type":"token_usage_record", "timestamp":at, "payload":{
            "thread_id":tid, "response_id":key, "usage":{"input_tokens":10,"cached_input_tokens":6,"output_tokens":2}}})
    index = home/"state_5.sqlite"
    with closing(sqlite3.connect(index)) as db, db:
        db.execute("CREATE TABLE threads(id, rollout_path)")
        db.execute("CREATE TABLE thread_spawn_edges(parent_thread_id,child_thread_id)")
        for name in ("root", "child", "grand"):
            log = home/(name+".jsonl")
            db.execute("INSERT INTO threads VALUES(?,?)", (name,str(log)))
            log.write_text("\n".join([record(name,"self"),record(name,"self"),record("foreign","inherited"),
                                      record(name,"too late",end)]))
        db.executemany("INSERT INTO thread_spawn_edges VALUES(?,?)",[("root","child"),("child","grand")])
    args = ["usage","--thread","root","--thread","child","--since",start,"--until",end]
    result = cli(root, "lead", *args)
    assert result["complete"] and result["window_seconds"] == 60 and len(result["participants"]) == 3
    assert result["measured"] == dict(responses=3,input=30,cached_input=18,uncached_input=12,output=6)
    summary = cli(root, "lead", *args, "--summary")
    assert summary == {**{k:v for k,v in result.items() if k != "participants"}, "participant_count":3}
    with closing(sqlite3.connect(index)) as db, db:
        db.execute("INSERT INTO thread_spawn_edges VALUES('root','missing')")
    gap = cli(root, "lead", *args)
    assert not gap["complete"] and gap["missing_chats"] == ["missing"]
    assert cli(root, "lead", *args, "--summary")["missing_chats"] == ["missing"]
    with closing(sqlite3.connect(index)) as db, db:
        db.execute("DROP TABLE thread_spawn_edges")
    assert not cli(root, "lead", *args)["descendants_indexed"]
    assert "timezone" in cli(root, "lead", *args[:-1], "2026-01-01T00:01:00", fail=True)


def concurrent_rpc(root):
    root.mkdir()
    init(root)
    baseline = cli(root, "lead", "inbox")["cursor"]
    env = dict(os.environ, CODEX_HOME=str(root.parent/"codex"), CODEX_THREAD_ID="lead")
    env.pop("LOOMHARNESS_HOME", None)
    team, client = SCRIPTS/"team.py", SCRIPTS/"hk_call.py"
    with subprocess.Popen([sys.executable, "-I", str(team), "serve"], cwd=root, env=env,
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as server:
        try:
            assert json.loads(server.stdout.readline())["ready"]
            def call(*args, actor=None):
                child = env.copy()
                child.pop("CODEX_THREAD_ID")
                if actor:
                    child["CODEX_THREAD_ID"] = actor
                output = subprocess.check_output([sys.executable, "-I", *map(str,args)], cwd=root.parent,
                                                 env=child, encoding="utf-8", timeout=15)
                return json.loads(output)
            assert call(client, "--project", root, "intake")["actor"] == "missing"
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(call, client, "--project", root, "--", "inbox", "--after", baseline, "--wait", "5",
                                      actor="lead")
                time.sleep(0.3)
                task = call(team, "--project", root, "rpc", "--", "add", "--title", "Independent edit",
                            "--role", "writer", "--scope", "one", "--criterion", "done", "--hours", ".1", actor="lead")
                assert pending.result()["dispatch"][0]["id"] == task["id"]
        finally:
            server.communicate("quit\n", timeout=10)


if __name__ == "__main__":
    with TemporaryDirectory() as temp:
        root = Path(temp)/"project"
        root.mkdir()
        board(root)
        metrics(root)
        plain_board(Path(temp)/"Quarterly report")
    with TemporaryDirectory() as temp:
        chat_usage(Path(temp)/"project")
    with TemporaryDirectory() as temp:
        concurrent_rpc(Path(temp)/"project")
    print("OK: isolated cursors, change waits, stop, dependencies, resources, role routing and window coverage")
