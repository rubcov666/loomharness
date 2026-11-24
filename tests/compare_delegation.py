"""Paired live Codex runs on synthetic files. No model calls without --live."""

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import random
import shutil
import subprocess
import sys
from threading import Timer

from verify_install import temporary_profile


KIT = Path(__file__).resolve().parents[1] / "loomharness"
# Standard credits per million uncached input, cached input and output tokens.
# Source and measurement date are recorded alongside the results in the README.
MODELS = {"gpt-6.2-astra": (200, 10, 1000), "gpt-6.2-sol": (50, 2.5, 250)}


@contextmanager
def app_server():
    with subprocess.Popen([shutil.which("codex"), "app-server", "--listen", "stdio://"],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          text=True, encoding="utf8") as server:
        seq = 0

        def rpc(method, params):
            nonlocal seq
            seq += 1
            timer = Timer(30, server.kill)
            timer.start()
            try:
                server.stdin.write(json.dumps({"id": seq, "method": method, "params": params}) + "\n")
                server.stdin.flush()
                for line in server.stdout:
                    reply = json.loads(line)
                    if reply.get("id") == seq:
                        if "error" in reply:
                            raise RuntimeError(f"{method}: {reply['error'].get('message')}")
                        return reply["result"]
                raise RuntimeError(f"No reply to {method}")
            finally:
                timer.cancel()

        try:
            rpc("initialize", {"clientInfo": {"name": "harness_benchmark", "version": "1"},
                               "capabilities": {"experimentalApi": True}})
            server.stdin.write('{"method":"initialized","params":{}}\n')
            server.stdin.flush()
            yield rpc
        finally:
            server.stdin.close()
            try:
                server.wait(timeout=3)
            except subprocess.TimeoutExpired:
                server.kill()


def allowance(rpc):
    data = rpc("account/rateLimits/read", {"excludeResetCreditDetails": True})
    bucket = (data.get("rateLimitsByLimitId") or {}).get("codex") or data["rateLimits"]
    return {str(window["windowDurationMins"]): (window["usedPercent"], window["resetsAt"])
            for key in ("primary", "secondary") if (window := bucket.get(key))}


def counters(thread):
    """Read only the rollout returned for this benchmark thread; never enumerate chat history."""
    last = model = effort = None
    spawns = []
    with Path(thread["path"]).open(encoding="utf8") as events:
        for line in events:
            row = json.loads(line)
            data = row.get("payload", {})
            if row["type"] == "turn_context":
                model = data.get("model", model)
                effort = data.get("effort", effort)
            if row["type"] == "response_item" and data.get("name") == "spawn_agent":
                args = json.loads(data["arguments"])
                spawns.append({key: args.get(key) for key in ("fork_turns", "model", "reasoning_effort")})
            if row["type"] == "event_msg" and data.get("type") == "token_count" and data.get("info"):
                last = data["info"]["total_token_usage"]
    if not last or model not in MODELS:
        raise RuntimeError(f"Missing token counters or unexpected model: {model}")
    fresh = last["input_tokens"] - last["cached_input_tokens"]
    quantities = [fresh, last["cached_input_tokens"], last["output_tokens"]]
    estimate = sum(q * rate for q, rate in zip(quantities, MODELS[model])) / 1_000_000
    return {"model": model, "effort": effort, "spawns": spawns, "input": last["input_tokens"],
            "cached": last["cached_input_tokens"], "output": last["output_tokens"],
            "standard_rate_estimate": round(estimate, 6)}


def usage(rpc, root):
    thread = rpc("thread/read", {"threadId": root, "includeTurns": True})["thread"]
    child_ids = {i for turn in thread["turns"] for item in turn["items"]
                 if item.get("tool") == "spawnAgent" for i in item["receiverThreadIds"]}
    listed = rpc("thread/list", {"ancestorThreadId": root, "sourceKinds": ["subAgent", "subAgentThreadSpawn"],
                                  "limit": 50})
    if listed.get("nextCursor"):
        raise RuntimeError("Unexpected number of descendants; cost totals would be incomplete")
    child_ids.update(t["id"] for t in listed["data"])
    rows = []
    for tid in [root, *sorted(child_ids)]:
        node = thread if tid == root else rpc("thread/read", {"threadId": tid})["thread"]
        row = counters(node)
        row["child"] = tid != root
        try:
            server = rpc("account/usage/read", {"threadId": tid}).get("threadUsage")
        except RuntimeError:
            server = None
        row["server_credits"] = server["estimatedUsageCreditsMicros"] / 1_000_000 if server else None
        row["billing_models"] = sorted({g["model"] for g in server["groups"] if g.get("model")}) if server else []
        rows.append(row)
    return rows


def inspection(folder, case):
    # Meaning depends on order and negation. The same vocabulary appears in
    # compliant records, actual breaches, aborted releases and planned actions.
    routine = (
        "The customer signed for this item after the receiving clerk checked the seal.",
        "Delivery waited at the counter until the seal check came through.",
        "The clerk initially planned to release before verification, then held it until the seal was verified.",
        "The driver handed over the manifest before the seal check. The item stayed in the depot.",
        "The seal had already been verified by the time the customer received the item.",
        "The recipient asked for the parcel before verification. The clerk refused the request.",
    )
    violations = ("With the item already in the customer's hands, the clerk began checking the seal.",
                  "The customer took the parcel while seal verification was still pending.",
                  "When the item left with its recipient, nobody had yet checked the seal.")
    rng = random.Random(2401 if case == "inspection" else 3822)
    expected = {}
    for file, count in zip(("A", "B", "C"), (2, 0, 1)):
        target = sorted(rng.sample(range(3, 239), count))
        expected[file] = target
        lines = []
        for n in range(1, 241):
            event = rng.choice(violations if n in target else routine)
            lines.append(f"Delivery {rng.randrange(10**8):08d} at bay {rng.randrange(1, 80)}. {event}")
        (folder / f"{file}.log").write_text("\n".join(lines) + "\n", encoding="utf8")
    return ("Read the three independent 240-entry inspection journals A.log, B.log and C.log by meaning. "
            "Which entries say the customer actually took the item before its seal was checked? "
            "Distinguish completed deliveries from proposals, paperwork and refusals. Return JSON keyed "
            "by A, B and C, with lists of original line numbers, including an empty list where appropriate."), expected


def fixtures(folder, case):
    if case == "short":
        (folder / "A.log").write_text("ledger: status=skipped\n", encoding="utf8")
        return "Read A.log and return JSON with status and line (integer).", {"status": "skipped", "line": 1}
    return inspection(folder, case)


def run(folder, case, arm, rpc):
    task, expected = fixtures(folder, case)
    mode = {"requested": "Delegate reading these data to one subagent, then verify its answer.\n",
            "solo": "Complete the task without subagents.\n"}.get(arm, "")
    prompt = (mode + task + "\nUse fixture_reader.read_fixture for benchmark data "
              "(file, optional literal find, or start and lines for a numbered excerpt). "
              "Return only the requested JSON.")
    reader = Path(__file__).with_name("fixture_reader.py").resolve()
    args = [shutil.which("codex"), "exec", "--enable", "hooks", "--skip-git-repo-check", "--sandbox", "read-only",
            "-m", "gpt-6.2-astra", "-c", 'model_reasoning_effort="medium"', "-c", 'service_tier="default"',
            "-c", "mcp_servers.fixture_reader.command=" + json.dumps(Path(sys.executable).as_posix()),
            "-c", 'mcp_servers.fixture_reader.tools.read_fixture.approval_mode="approve"',
            "-c", "mcp_servers.fixture_reader.tools.read_fixture.output_token_limit=16000",
            "-c", "mcp_servers.fixture_reader.args=" + json.dumps(["-I", reader.as_posix(), folder.as_posix()]),
            "--json", "-"]
    before = allowance(rpc)
    result = subprocess.run(args, input=prompt.encode(), capture_output=True, cwd=folder, timeout=300)
    events = [json.loads(s) for s in result.stdout.splitlines() if s.strip()]
    root = next(e["thread_id"] for e in events if e.get("type") == "thread.started")
    print("thread", root, case, arm, flush=True)
    if result.returncode:
        raise RuntimeError(f"Codex exited {result.returncode}: {[e.get('type') for e in events[-4:]]}")
    answer = next(e["item"]["text"] for e in reversed(events) if e.get("type") == "item.completed"
                  and e.get("item", {}).get("type") == "agent_message").strip()
    if answer.startswith(chr(96) * 3):
        answer = "\n".join(answer.splitlines()[1:-1])
    try:
        correct = json.loads(answer) == json.loads(json.dumps(expected))
    except ValueError:
        correct = False
    after = allowance(rpc)
    delta = {w: v[0] - before[w][0] for w, v in after.items() if w in before and v[1] == before[w][1]}
    rows = usage(rpc, root)
    delegated = arm == "requested"
    wanted = {"fork_turns": "none", "model": "gpt-6.2-sol", "reasoning_effort": "low"}
    routing_ok = ([(r["model"], r["effort"]) for r in rows] ==
                  ([("gpt-6.2-astra", "medium"), ("gpt-6.2-sol", "low")] if delegated
                   else [("gpt-6.2-astra", "medium")])
                  and [s for row in rows for s in row["spawns"]] == ([wanted] if delegated else []))
    print(json.dumps(dict(case=case, arm=arm, correct=correct, routing_ok=routing_ok, threads=rows,
                          account_window_delta_pp=delta)), flush=True)
    if not correct:
        print("Unexpected synthetic answer:", answer[:600], flush=True)
    return correct and routing_ok


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Use the signed-in account's allowance.")
    parser.add_argument("--case", choices=["inspection", "inspection-b", "short"])
    parser.add_argument("--arm", choices=["default", "requested", "solo", "auto"],
                        help="Use ordinary wording, explicitly request a worker, or prohibit workers (solo). "
                             "The older auto spelling also uses ordinary wording.")
    args = parser.parse_args()
    if not args.live:
        parser.print_help()
        sys.exit(0)
    success = True
    with temporary_profile() as folder, app_server() as rpc:
        if (rpc("account/read", {"refreshToken": False}).get("account") or {}).get("type") != "chatgpt":
            sys.exit("Sign in with ChatGPT to measure subscription usage.")
        installed = Path.home() / ".agents/skills/loomharness"
        for name in ("SKILL.md", "scripts/harness.py"):
            if (installed / name).read_text(encoding="utf8") != (KIT / name).read_text(encoding="utf8"):
                sys.exit("Install this release before running its live benchmark.")
        (folder / ".codex").mkdir()
        (folder / ".codex/harness.enabled").touch()
        for case in ([args.case] if args.case else ["inspection", "inspection-b", "short"]):
            arms = ["requested", "default"] if case == "inspection" else ["default", "requested"]
            for arm in ([args.arm] if args.arm else arms):
                success &= run(folder, case, arm, rpc)
    sys.exit(0 if success else 1)
