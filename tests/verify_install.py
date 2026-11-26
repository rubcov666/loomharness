"""Exercise a real installation in an isolated user profile on the current OS."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager


REPO = Path(os.environ.get("LOOMHARNESS_REPO", Path(__file__).resolve().parents[1]))
EVENTS = {"SessionStart", "Stop", "UserPromptSubmit", "PreToolUse"}


def check_release_hashes():
    # Every shipped file at the current commit must be a known previous release,
    # otherwise the next installer run refuses to upgrade (reports a user edit).
    installer = runpy.run_path(str(REPO / "install.py"))
    previous = installer["PREVIOUS_RELEASES"]
    for name in installer["FILES"]:
        data = (REPO / "loomharness" / name).read_bytes().replace(b"\r\n", b"\n")
        digest = hashlib.sha256(data).hexdigest()
        assert digest in previous.get(name, ()), f"add the current hash of {name} to PREVIOUS_RELEASES"


@contextmanager
def temporary_profile():
    fixture = tempfile.TemporaryDirectory()
    try:
        yield Path(fixture.name).resolve()
    finally:
        # On Windows a Codex helper can briefly retain its working directory.
        deadline = time.monotonic() + 10
        while True:
            try:
                fixture.cleanup()
                break
            except PermissionError as exc:
                if getattr(exc, "winerror", None) != 32 or time.monotonic() >= deadline:
                    raise
                time.sleep(0.2)


def exercise(base, custom_home, check_registry):
    home = base / ("Profile Ω O'Brien $test" if custom_home else "home")
    project = base / "project-Ω"
    project.mkdir(parents=True)
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), TMPDIR=str(base),
               TMP=str(base), TEMP=str(base), PYTHONIOENCODING="utf-8")
    codex_home = home / ".codex"
    env.pop("CODEX_HOME", None)
    if custom_home:
        codex_home = base / "custom codex"
        env["CODEX_HOME"] = str(codex_home)
    config = codex_home / "hooks.json"
    foreign = {"hooks": [{"type": "command", "command": "echo existing"}]}
    if custom_home:
        config.parent.mkdir()
        config.write_text(json.dumps({"hooks": {"SessionStart": [foreign]}}), encoding="utf8")

    install = [sys.executable, "-I", "-X", "utf8", str(REPO / "install.py")]
    subprocess.run(install, env=env, cwd=project, check=True, capture_output=True)
    first = config.read_bytes()
    subprocess.run(install, env=env, cwd=project, check=True, capture_output=True)
    assert config.read_bytes() == first
    groups = json.loads(first)["hooks"]
    assert set(groups) == EVENTS
    assert sum(map(len, groups.values())) == 4 + int(custom_home)
    if custom_home:
        assert groups["SessionStart"][0] == foreign
    dest = home / ".agents/skills/loomharness"
    for relative in ("SKILL.md", "agents/openai.yaml", "scripts/harness.py", "references/helper.md",
                     "scripts/context.py", "scripts/ssh_windows.py", "scripts/view.py", "references/context.md",
                     "scripts/plan.py", "scripts/javac_cache.py", "references/verification.md",
                     "scripts/team.py", "scripts/team_usage.py", "scripts/team_flow.py", "scripts/model_policy.py", "references/team.md",
                     "scripts/initiative.py", "scripts/inbox.py", "scripts/metrics.py", "scripts/records.py",
                     "scripts/review_queue.py", "scripts/team_models.py", "scripts/record_wait.js",
                     "references/adaptive-team.md",
                     "references/organization.md", "references/initiative.md",
                     "references/review.md", "references/delivery.md", "references/executor.md",
                     "references/telemetry.md", "references/commands.md"):
        assert (dest / relative).read_bytes() == (REPO / "loomharness" / relative).read_bytes()

    def doctor(session, *args):
        command = [sys.executable, "-I", "-X", "utf8", str(dest / "scripts/harness.py"), "doctor", *args]
        result = subprocess.run(command, env=dict(env, CODEX_THREAD_ID=session), cwd=project,
                                capture_output=True, check=True, timeout=25)
        return json.loads(result.stdout)

    offline = ("--codex", str(base / "absent-codex"))
    before = doctor("fixture", *offline)
    assert before["projectEnabled"] is None and before["lastHook"] is None
    assert before["codexRegistry"]["status"] == "unavailable" and before["session"] == "fixture"
    assert before["userHookFile"]["status"] == "read" and set(before["userHookFile"]["events"]) == EVENTS
    assert not (codex_home / "loomharness/projects.sqlite3").exists()

    def invoke(name, **fields):
        command = groups[name][-1]["hooks"][0]["command"]
        event = {"hook_event_name": name, "cwd": str(project), "session_id": "fixture", **fields}
        result = subprocess.run(command, shell=True, input=json.dumps(event).encode(), cwd=project,
                                env=env, capture_output=True, timeout=12, check=True)
        return json.loads(result.stdout) if result.stdout.strip() else None

    subprocess.run(["git", "init", "-q", "-b", "install-test"], cwd=project, env=env, check=True)
    question = "Enable LoomHarness hooks for this project?"
    assert question in invoke("SessionStart")["hookSpecificOutput"]["additionalContext"]
    assert invoke("Stop", last_assistant_message=question) is None
    assert "Project hooks enabled." in invoke("UserPromptSubmit", prompt="Да")["hookSpecificOutput"]["additionalContext"]
    (project / "PLAN.md").write_text("Next step: check the import.", encoding="utf8")
    assert "1 | Next step" in invoke("UserPromptSubmit", prompt="What's next?")["hookSpecificOutput"]["additionalContext"]
    # Run the exact command the enabled hook gives the agent, even in a path
    # with spaces, apostrophes and a dollar sign, without resolving Python on PATH.
    message = invoke("UserPromptSubmit", prompt="Continue", session_id="view-smoke")["hookSpecificOutput"]["additionalContext"]
    prefix = message.splitlines()[-1]
    (project / "sample.json").write_bytes(b'[{"id":7}]')
    shell = [shutil.which("powershell"), "-NoProfile", "-Command"] if os.name == "nt" else ["/bin/sh", "-c"]
    result = subprocess.run([*shell, prefix + " sample.json"], cwd=project, env=env, capture_output=True, check=True)
    assert json.loads(result.stdout) == [{"id": 7}]
    check = invoke("PreToolUse", tool_name="apply_patch", tool_input={"command": "*** Add File: STATE.json"})
    assert check["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "install-test" in invoke("Stop")["systemMessage"]
    report = doctor("fixture", *(() if check_registry else offline))
    assert report["projectEnabled"] is True and Path(report["project"]) == project
    assert report["lastHook"]["event"] == "Stop" and report["lastHook"]["client"] == "codex"
    assert doctor("unseen-chat", *offline)["lastHook"] is None
    if check_registry:
        registry = report["codexRegistry"]
        assert registry["status"] == "queried" and not registry["errors"], report
        hooks = registry["hooks"]
        assert len(hooks) == 4 and all(h["enabled"] and h["source"] == "user" for h in hooks), hooks
        assert all(h["trustStatus"] in ("managed", "trusted", "untrusted", "modified") for h in hooks)
        assert {h["eventName"].casefold() for h in hooks} == {e.casefold() for e in EVENTS}

    skill = dest / "SKILL.md"
    skill.write_text("local edits", encoding="utf8")
    assert subprocess.run(install, env=env, cwd=project, capture_output=True).returncode != 0
    assert skill.read_text(encoding="utf8") == "local edits" and config.read_bytes() == first


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", action="store_true", help="Also verify registration with the Codex CLI")
    args = parser.parse_args()
    check_release_hashes()
    with temporary_profile() as base:
        exercise(base / "default", False, False)
        exercise(base / "custom", True, args.codex)
    print("PASS: release hash table, fresh install, repeat install, all four commands, custom paths and local edits")
