"""Run the portable compaction policy through the CLI, MCP, and actual hook entry points."""

import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
from unittest.mock import patch

from verify_install import REPO, temporary_profile


SCRIPTS = REPO / "loomharness" / "scripts"


def run(script, *args, stdin="", cwd=None, env=None, ok=True):
    proc = subprocess.run([sys.executable, "-I", "-X", "utf8", str(SCRIPTS / script), *args],
                          input=stdin, capture_output=True, encoding="utf8", cwd=cwd, env=env, timeout=15)
    assert (proc.returncode == 0) == ok, (proc.args, proc.stdout, proc.stderr)
    return proc.stdout


def budget(*args):
    return json.loads(run("history.py", "budget", *args))


def check_budgets():
    assert budget("--used", "100") == {"action": "measure", "missing": "window_tokens"}
    assert budget("--window", "32000")["action"] == "measure"
    for window, trigger, target in [(1000000, 256000, 80000), (128000, 96000, 30000), (32000, 24000, 7500)]:
        args = ["--window", str(window)]
        assert budget(*args, "--used", str(trigger - 1))["action"] == "keep"
        review = budget(*args, "--used", str(trigger))
        assert (review["action"], review["trigger_tokens"], review["target_tokens"]) == ("compact", trigger, target)
        assert review["break_even_calls"] == 2
    assert budget("--window", "32000", "--target", "80000")["target_tokens"] == 7500
    for client, key in [("codex", "args"), ("claude", "env"), ("gemini", "settings")]:
        info = budget("--window", "1000000", "--client", client)["adapter"]
        assert info["kind"] == "native"
        if client == "gemini":
            assert info[key]["model"]["compressionThreshold"] == 0.256
        else:
            assert "256000" in str(info[key])
    for client in ("new-vendor-cli", "claude"):
        assert budget("--window", "32000", "--client", client)["adapter"] == {"kind": "portable"}
    args = ["--window", "10000", "--trigger", "1000", "--used", "1200"]
    assert budget(*args, "--protected", "600")["target_tokens"] == 600
    assert budget(*args, "--protected", "1000")["action"] == "preserve"
    run("history.py", "budget", *args, "--protected", "1300", ok=False)
    run("history.py", "budget", "--window", "0", ok=False)

    args = ["--window", "1000000", "--used", "256000"]
    near_end = budget(*args, "--remaining-calls", "1")
    assert near_end["reason"] == "compaction_would_not_pay_back" and near_end["saved_per_call"] == 176000
    assert near_end["compaction_tokens"] == 260096
    assert budget(*args, "--compaction-tokens", "352000")["break_even_calls"] == 3  # Ties keep the history.
    assert budget(*args, "--reread-tokens", "93000")["action"] == "keep"
    assert budget(*args, "--remaining-calls", "1", "--compaction-tokens", "6000")["action"] == "compact"
    assert budget(*args, "--protected", "210000")["action"] == "keep"
    pinned = budget("--window", "1000000", "--used", "700000", "--protected", "300000")
    assert pinned["action"] == "compact" and pinned["estimated_next_input_tokens"] == 304096
    assert budget("--window", "1000000", "--used", "880000", "--remaining-calls", "1")["reason"] == "context_window_pressure"
    assert budget("--window", "1000000", "--used", "880000", "--remaining-calls", "0")["reason"] == "work_finished"
    assert budget("--window", "1000000", "--used", "880000", "--protected", "878000")["action"] == "preserve"
    native = budget(*args, "--remaining-calls", "1", "--client", "gemini")["adapter"]
    assert native["settings"]["model"]["compressionThreshold"] == 0.875
    native = budget(*args, "--remaining-calls", "3", "--compaction-tokens", "600000", "--client", "codex")["adapter"]
    assert "model_auto_compact_token_limit=280001" in native["args"]
    run("history.py", "budget", *args, "--remaining-calls", "-1", ok=False)


def group(tokens, *items, **options):
    return {"tokens": tokens, "messages": list(items), **options}


def check_launch():
    history = runpy.run_path(str(SCRIPTS / "history.py"))
    argv = ["history.py", "launch", "--window", "1000000", "--remaining-calls", "1", "codex", "--version"]
    # Simulate process replacement at the last boundary so the test never calls a provider CLI.
    with patch.object(sys, "argv", argv), patch("shutil.which", return_value="codex"), \
            patch("os.execve", side_effect=SystemExit(0)) as execute, patch("subprocess.run") as start:
        if os.name == "posix":
            try:
                history["main"]()
            except SystemExit as exit_code:
                assert exit_code.code == 0
            command = execute.call_args.args[1]
        else:
            history["main"]()
            command = start.call_args.args[0]
    assert command == ["codex", "-c", "model_auto_compact_token_limit=875000", "-c",
                       'model_auto_compact_token_limit_scope="total"', "--version"]


def check_replacement(folder):
    prefix = {"role": "system", "content": "Retain exception 12."}
    source = {"role": "user", "parts": [{"text": "Closed case. Date 2026-05-03. Source case/17."}]}
    call = {"role": "assistant", "content": [{"type": "tool_use", "id": "a", "name": "lookup"}]}
    answer = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a", "content": "42"}]}
    latest = {"role": "user", "parts": [{"inlineData": {"mimeType": "text/plain", "data": "YWJj"}}]}
    anchors = ["2026-05-03", "case/17"]
    turns = [group(195000, source, anchors=anchors), group(50000, call, answer, keep=True), group(1000, latest)]
    snapshot = {"window_tokens": 1000000, "prefix": group(10000, prefix), "turns": turns}
    path, note = folder / "history-Ω.json", folder / "summary-Ω.json"
    def save(p, obj):
        p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf8")
    def plan():
        save(path, snapshot)
        return json.loads(run("history.py", "plan", str(path)))
    selected = plan()
    assert selected["summarize_turns"] == 1 and selected["retained_tokens"] == 61000
    summary = {"role": "user", "parts": [{"text": "Closed 2026-05-03. Evidence case/17."}]}
    checkpoint = dict(group(4000, summary), source_sha256=selected["source_sha256"])
    save(note, checkpoint)
    original = path.read_bytes()
    apply_args = ("apply", str(path), str(note))
    outgoing = json.loads(run("history.py", *apply_args))
    assert outgoing["action"] == "replace" and outgoing["replacement_tokens"] == 65000
    assert outgoing["messages"] == [prefix, summary, call, answer, latest]
    assert path.read_bytes() == original and json.loads(run("history.py", *apply_args)) == outgoing
    assert selected["required_anchors"] == anchors
    save(note, dict(checkpoint, messages=[{"role": "user", "content": "Closed 2026-05-03. Evidence case/71."}]))
    refused = json.loads(run("history.py", *apply_args))
    assert refused["reason"] == "missing_anchors" and refused["missing_anchors"] == ["case/17"]
    assert "messages" not in refused
    save(note, checkpoint)
    snapshot["remaining_calls"] = 1
    assert plan()["action"] == "keep"
    assert json.loads(run("history.py", *apply_args))["action"] == "replace"  # An existing summary costs no new call.
    snapshot["reread_tokens"] = 195000
    assert plan()["action"] == json.loads(run("history.py", *apply_args))["action"] == "keep"
    del snapshot["remaining_calls"], snapshot["reread_tokens"]
    # The reference and its exception survive a second compaction cycle.
    snapshot["turns"] = [group(4000, summary, anchors=anchors), group(196000, source), *turns[1:]]
    second = plan()
    assert second["summarize_turns"] == 2
    run("history.py", *apply_args, ok=False)
    checkpoint["source_sha256"] = second["source_sha256"]
    save(note, checkpoint)
    assert json.loads(run("history.py", *apply_args))["messages"] == outgoing["messages"]
    checkpoint["tokens"] = 200000
    save(note, checkpoint)
    refused = json.loads(run("history.py", *apply_args))
    assert refused["action"] == "preserve" and "messages" not in refused
    turns[1]["tokens"] = 120000
    selected = plan()
    assert selected["target_tokens"] == selected["retained_tokens"] == 131000
    save(note, dict(group(4000, summary), source_sha256=selected["source_sha256"]))
    bigger = json.loads(run("history.py", *apply_args))
    assert bigger["replacement_tokens"] == 135000 and bigger["messages"] == outgoing["messages"]
    snapshot["turns"][0]["keep"] = True
    assert plan()["action"] == "preserve"
    snapshot["turns"] = [turns[-1]]
    assert plan()["action"] == "keep"

    # Only whole groups can be removed. A large completed group can make the real plan
    # profitable even when the coarse budget based on the preferred target predicts a loss.
    snapshot = {"window_tokens": 1000000, "prefix": group(1000, prefix), "turns": [
        group(254000, source, anchors=anchors), group(1000, latest)], "remaining_calls": 1,
        "compaction_tokens": 230000}
    exact = plan()
    assert exact["action"] == "compact" and exact["saved_per_call"] == 249904
    snapshot["turns"][0]["anchors"] = ["invented reference"]
    save(path, snapshot)
    run("history.py", "plan", str(path), ok=False)


def check_native_requests(folder):
    cases = [
        ("input", {"instructions": "Keep case 17.", "tools": [{"type": "function", "name": "lookup",
                   "parameters": {"type": "object", "properties": {}}}]},
         lambda text: {"role": "user", "content": text}, {"role": "assistant", "content": "Read."}),
        ("messages", {"system": [{"type": "text", "text": "Keep case 17."}],
                      "tools": [{"name": "lookup", "input_schema": {"type": "object", "properties": {}}}]},
         lambda text: {"role": "user", "content": [{"type": "text", "text": text}]},
         {"role": "assistant", "content": [{"type": "text", "text": "Read."}]}),
        ("contents", {"systemInstruction": {"parts": [{"text": "Keep case 17."}]},
                      "tools": [{"functionDeclarations": [{"name": "lookup"}]}]},
         lambda text: {"role": "user", "parts": [{"text": text}]}, {"role": "model", "parts": [{"text": "Read."}]}),
    ]
    path, note = folder / "request.json", folder / "checkpoint.json"
    for field, fixed, message, assistant in cases:
        old = message("Case 17 closed. " + "Archived log entry. " * 100)
        recent = message("Which case was closed?")
        brief = message("Case 17 closed. Source case/17.")
        snapshot = {"window_tokens": 128000, "prefix": group(8000, revision="tools-v1"),
                    "turns": [group(88000, old, assistant), group(5000, recent)]}
        path.write_text(json.dumps(snapshot), encoding="utf8")
        selection = json.loads(run("history.py", "plan", str(path)))
        summary = group(1000, brief, assistant, source_sha256=selection["source_sha256"])
        note.write_text(json.dumps(summary), encoding="utf8")
        result = json.loads(run("history.py", "apply", str(path), str(note)))
        assert result["action"] == "replace" and result["replacement_tokens"] == 14000
        request = {**fixed, field: result["messages"]}
        assert request[field] == [brief, assistant, recent] and old not in request[field]
        assert all(request[key] == value for key, value in fixed.items())
        snapshot["prefix"]["revision"] = "tools-v2"
        path.write_text(json.dumps(snapshot), encoding="utf8")
        run("history.py", "apply", str(path), str(note), ok=False)


def check_mcp_and_hooks(folder):
    messages = [
        {"id":1, "method":"initialize", "params":{"protocolVersion":"2025-06-18"}},
        {"method":"notifications/initialized"},
        {"id":2, "method":"ping"},
        {"id":3, "method":"initialize", "params":{"protocolVersion":"unknown"}},
        {"id":4, "method":"tools/call", "params":{"name":"harness_code_map",
                                                  "arguments":{"cwd":str(folder), "query":"file", "limit":0}}},
        {"id":5, "method":"tools/list"},
    ]
    wire = "".join(json.dumps(dict(m, jsonrpc="2.0")) + "\n" for m in messages)
    replies = {r["id"]:r["result"] for r in map(json.loads, run("mcp_server.py", stdin=wire).splitlines())}
    assert replies[1]["protocolVersion"] == "2025-06-18" and replies[2] == {}
    assert replies[3]["protocolVersion"] == "2025-11-25" and replies[4]["isError"]
    assert {t["name"] for t in replies[5]["tools"]} == {
        "harness_context_budget", "harness_view", "harness_find", "harness_code_map", "harness_metrics"}
    def rpc(args):
        message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": "harness_context_budget", "arguments": args}}
        return json.loads(run("mcp_server.py", stdin=json.dumps(message) + "\n"))["result"]
    result = rpc({"window_tokens": 1000000, "used_tokens": 256000, "client": "another-model-host"})
    assert not result["isError"] and json.loads(result["content"][0]["text"])["action"] == "compact"
    assert rpc({"window_tokens": True})["isError"]
    assert rpc({"remaining_calls": False})["isError"]
    finishing = {"window_tokens": 1000000, "used_tokens": 256000, "remaining_calls": 1}
    assert json.loads(rpc(finishing)["content"][0]["text"])["action"] == "keep"

    env = dict(os.environ, CODEX_HOME=str(folder / "profile"))
    run("harness.py", "enable", cwd=folder, env=env)
    def hook(client, **fields):
        event = {"hook_event_name": "BeforeAgent" if client == "gemini" else "UserPromptSubmit",
                 "session_id": "same-id", "cwd": str(folder), "prompt": "Continue the task.", **fields}
        return run("harness.py", "hook", "--cli", client, env=env, stdin=json.dumps(event))
    observations = {
        "portable": {"context_usage": {"window_tokens": 1000000, "used_tokens": 256000}},
        "codex": {"tokenUsage": {"modelContextWindow": 1000000, "last": {"inputTokens": 256000,
                                  "cachedInputTokens": 100000}, "total": {"inputTokens": 4000000}}},
        "claude": {"context_window": {"context_window_size": 1000000, "current_usage": {
            "input_tokens": 6000, "cache_creation_input_tokens": 50000, "cache_read_input_tokens": 200000}}},
        "gemini": {"window_tokens": 1000000, "usageMetadata": {"promptTokenCount": 256000,
                   "cachedContentTokenCount": 100000, "totalTokenCount": 400000}},
    }
    assert "256000" not in hook("portable", context_usage=finishing)
    for client, fields in observations.items():
        first = json.loads(hook(client, **fields))["hookSpecificOutput"]["additionalContext"]
        assert "256000" in first and "80000" in first, client
        assert not hook(client, **fields), client
        assert not hook(client, context_usage={"used_tokens": 80000, "window_tokens": 1000000})
        assert "256000" in hook(client, **fields)
    assert not hook("portable", context_usage={"used_tokens": True, "window_tokens": 1000000})
    assert not hook("codex", tokenUsage={"total": {"inputTokens": 9000000}})
    assert not hook("claude", context_window={"context_window_size": 1000000,
                                             "current_usage": {"input_tokens": 0}})
    assert not hook("claude", **observations["claude"])
    run("harness.py", "disable", cwd=folder, env=env)
    assert not hook("portable", **observations["portable"])


if __name__ == "__main__":
    check_budgets()
    check_launch()
    with temporary_profile() as folder:
        check_replacement(folder)
        check_native_requests(folder)
        check_mcp_and_hooks(folder)
    print("PASS: portable thresholds, native settings, two compactions, protected tool turns, hooks and MCP")
