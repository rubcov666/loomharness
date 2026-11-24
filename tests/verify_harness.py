from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile


script = Path(sys.argv[1]).resolve()

with tempfile.TemporaryDirectory() as fixture:
    root = Path(fixture) / "Рабочая папка"
    root.mkdir()
    temp = Path(fixture) / "temp"
    temp.mkdir()
    env = dict(os.environ, TEMP=str(temp), TMP=str(temp), TMPDIR=str(temp),
               CODEX_HOME=str(temp/"profile"), CODEX_THREAD_ID="test-thread")

    def run(*args, raw=None, ok=True):
        result = subprocess.run([sys.executable, "-I", "-X", "utf8", str(script), *args],
                                input=raw, encoding="utf8", capture_output=True, env=env, cwd=root)
        assert (result.returncode == 0) == ok, result
        return result.stdout

    def event(name, session="test-thread", tool="apply_patch", target="STATE.json", cwd=None, **fields):
        data = {"cwd": str(cwd or root), "hook_event_name": name, "session_id": session,
                "tool_name": tool, "tool_input": {"command": f"*** Begin Patch\n*** Update File: {target}\n*** End Patch"}, **fields}
        return json.loads(run("hook", raw=json.dumps(data)) or "null")

    def denied(**params):
        return event("PreToolUse", **params)["hookSpecificOutput"]["permissionDecision"] == "deny"

    question = "Включить хуки LoomHarness для этого проекта?"
    def note(result):
        return result["hookSpecificOutput"]["additionalContext"]
    english = Path(fixture) / "English project"
    english.mkdir()
    en_question = "Enable LoomHarness hooks for this project?"
    initial = note(event("SessionStart", cwd=english))
    assert question in initial and en_question in initial and "LoomHarness on" in initial
    # A bare answer belongs only to the preceding question in this session and project.
    event("Stop", last_assistant_message="A quote: " + en_question, cwd=english)
    assert event("UserPromptSubmit", prompt="Yes", cwd=english) is None
    event("Stop", last_assistant_message=en_question, cwd=english)
    assert event("UserPromptSubmit", session="different", prompt="Yes", cwd=english) is None
    assert event("UserPromptSubmit", prompt="yes") is None
    event("Stop", last_assistant_message=en_question, cwd=english)
    assert "Project hooks enabled." in note(event("UserPromptSubmit", prompt="Yes!", cwd=english))
    assert event("UserPromptSubmit", prompt='"LoomHarness off"', cwd=english) is None
    assert "Project hooks disabled." in note(event("UserPromptSubmit", prompt="LoomHarness off", cwd=english))
    assert event("SessionStart", cwd=english) is None
    assert "Project hooks enabled." in note(event("UserPromptSubmit", prompt="LoomHarness on", cwd=english))
    assert question in note(event("SessionStart"))
    assert event("UserPromptSubmit", prompt="да") is None
    other = Path(fixture)/"Другой проект"
    other.mkdir()
    event("Stop", last_assistant_message=question, cwd=other)
    assert event("UserPromptSubmit", prompt="нет", session="other-thread", cwd=other) is None
    assert "Project hooks disabled." in note(event("UserPromptSubmit", prompt="Нет.", cwd=other))
    assert event("SessionStart", session="new-thread", cwd=other) is None
    assert event("PreToolUse", cwd=other) is None
    assert not list(other.iterdir())
    assert event("UserPromptSubmit", prompt="да", cwd=other) is None
    assert "Project hooks enabled." in note(event("UserPromptSubmit", prompt="Включи хуки LoomHarness", cwd=other))
    assert "loomharness" in note(event("SessionStart", cwd=other, session="other-chat"))
    assert "Project hooks disabled." in note(event("UserPromptSubmit", prompt="Отключи хуки LoomHarness", cwd=other))
    nested = other/"nested"
    nested.mkdir()
    assert question in note(event("SessionStart", cwd=nested))
    (other/".git").mkdir()
    assert event("SessionStart", cwd=nested) is None
    (nested/".git").mkdir()
    assert question in note(event("SessionStart", cwd=nested))
    # A quoted conversation or a yes from a different project cannot grant consent.
    event("Stop", last_assistant_message="Пример: "+question)
    assert event("UserPromptSubmit", prompt="да") is None
    event("Stop", last_assistant_message=question)
    assert event("UserPromptSubmit", prompt="да", cwd=nested) is None
    assert event("UserPromptSubmit", prompt="да") is None
    assert question in note(event("SessionStart", source="compact"))
    event("Stop", last_assistant_message=question)
    event("Stop", session="overlapping", last_assistant_message=question)
    first_enabled = note(event("UserPromptSubmit",prompt="Да!"))
    by_request = "only on an explicit request in this task"
    assert "Project hooks enabled." in first_enabled and by_request in first_enabled and "scripts/view.py" in first_enabled
    assert "rg -n -C 2" in first_enabled and "30 seconds" in first_enabled
    assert "references/team.md" in first_enabled
    assert not (Path(env["CODEX_HOME"]) / "loomharness" / "teams").exists()
    # A repeat SessionStart of the same chat injects nothing (receipt dedup).
    assert event("SessionStart") is None
    assert event("UserPromptSubmit", prompt="Продолжи разбор") is None
    old_chat = {"session": "already-open", "prompt": "Проверь все журналы"}
    old_key = "project-v8\0already-open\0" + os.path.normcase(str(root.resolve()))
    with closing(sqlite3.connect(Path(env["CODEX_HOME"]) / "loomharness/projects.sqlite3")) as db, db:
        db.execute("INSERT INTO delegation_notices VALUES (?)", (hashlib.sha256(old_key.encode()).digest(),))
    new_hint = note(event("UserPromptSubmit", **old_chat))
    assert by_request in new_hint and "rg -n -C 2" in new_hint and "30 seconds" in new_hint
    assert "references/team.md" in new_hint
    assert event("UserPromptSubmit", **old_chat) is None
    subprocess.run([sys.executable, "-I", "-X", "utf8", str(script.with_name("team.py")), "init",
                    "--goal", "Review first request", "--simple-model", "simple", "--strong-model", "strong"],
                   env=env, cwd=root, check=True, capture_output=True)
    # A previously notified team chat gets the corrected handoff once on its next prompt.
    old_key = "team-org-v5\0already-open\0" + os.path.normcase(str(root.resolve()))
    with closing(sqlite3.connect(Path(env["CODEX_HOME"]) / "loomharness/projects.sqlite3")) as db, db:
        db.execute("INSERT INTO delegation_notices VALUES (?)", (hashlib.sha256(old_key.encode()).digest(),))
    team_hint = note(event("UserPromptSubmit", **old_chat))
    assert "team.py intake" in team_hint and "The coordinator asks the owner" in team_hint
    assert event("UserPromptSubmit", **old_chat) is None
    assert "Project hooks enabled." in note(event("UserPromptSubmit",session="overlapping",prompt="нет"))
    legacy = Path(fixture)/"legacy"
    (legacy/".codex").mkdir(parents=True)
    (legacy/".codex/harness.enabled").touch()
    assert by_request in note(event("SessionStart",cwd=legacy))
    event("UserPromptSubmit",prompt="Отключи хуки LoomHarness",cwd=legacy)
    assert event("SessionStart",cwd=legacy) is None
    run("disable")
    assert event("PreToolUse") is None
    run("enable")
    run("enable")
    assert "вне Git" in run("status")
    # The project is a team board now, so the hint version changed: the first
    # SessionStart of this chat gets the updated hint once, a repeat none.
    startup = note(event("SessionStart"))
    assert event("SessionStart") is None
    assert "scripts/view.py" in startup and "loomharness" in startup and by_request in startup
    assert "Git:" not in startup
    assert event("Stop") is None
    assert event("UserPromptSubmit", prompt="Разбери все журналы и укажи ошибки") is None
    assert denied(target="docs/features.json")
    assert event("PreToolUse", target="docs/readme.md") is None
    run("read", "missing.log", ok=False)
    evidence = root / "тест проверки.log"
    evidence.write_text("Начало\nК1 PASS\nШум\nК1 FAIL\n", encoding="utf8")
    (root / ".codex").mkdir(exist_ok=True)
    (root / ".codex/harness-plan.json").write_text(json.dumps({"state_files": ["tracking/decisions.csv"]}))
    decisions = root / "tracking/decisions.csv"
    decisions.parent.mkdir()
    decisions.write_text("id,status\n1,approved\n")
    assert denied(target=str(decisions))
    assert event("PreToolUse", target="tracking/summary.csv") is None
    run("read", str(decisions), ok=False)
    run("read", evidence.name, "--find", "К2", ok=False)
    assert denied()
    page = run("read", evidence.name, "--find", "К1", "--lines", "1")
    assert "2: К1 PASS" in page and "--start 4" in page and "Шум" not in page
    assert not event("PreToolUse")
    assert event("PreToolUse", target=str(decisions)) is None
    assert run("read", evidence.name, "--find", "К1", "--start", "4").strip() == "4: К1 FAIL"
    assert run("read", evidence.name, "--find", "К1").splitlines() == ["2: К1 PASS", "4: К1 FAIL"]
    evidence.write_text("К1 выполнен\nК2 выполнен\n", encoding="utf8")
    run("read", evidence.name, "--start", "10", ok=False)
    assert denied()
    assert "2: К2 выполнен" in run("read", evidence.name, "--start", "2", "--lines", "1")
    assert event("PreToolUse") is None
    assert denied(session="other-thread")
    evidence.write_text("новый результат", encoding="utf8")
    assert denied()
    run("read", evidence.name)
    receipts, = temp.glob("codex-harness-evidence*")
    receipt, = receipts.iterdir()
    old = json.loads(receipt.read_text())
    old["at"] -= 1900
    receipt.write_text(json.dumps(old))
    assert denied()
    subprocess.run(["git", "init", "-q", "-b", "test-harness"], cwd=root, check=True)
    sub = root / "subdir"
    sub.mkdir()
    assert denied(cwd=sub)
    (root / "AGENT_STOP").touch()
    assert denied(tool="Bash")
    assert denied(tool="apply_patch", target="docs/readme.md")
    stopped = note(event("SessionStart"))
    assert "AGENT_STOP" in stopped and by_request not in stopped
    assert by_request not in note(event("UserPromptSubmit", prompt="Включи хуки LoomHarness"))
    run("read", evidence.name, ok=False)
    (root / "AGENT_STOP").unlink()
    assert "test-harness" in run("status")
    assert "test-harness" in event("Stop")["systemMessage"]
    print("PASS: inactive folder, idempotent enable, Git and non-Git, session start and stop, "
          "patch selection, evidence content, isolation, expiry, changes, AGENT_STOP and resume.")
