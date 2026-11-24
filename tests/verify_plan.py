"""Exercise the prompt hook through its normal isolated-process entry point."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


SCRIPT = Path(os.environ.get("LOOMHARNESS_REPO", Path(__file__).resolve().parents[1])) / "loomharness/scripts/harness.py"
with tempfile.TemporaryDirectory() as folder:
    base = Path(folder)
    root = base / "project"
    root.mkdir()
    env = dict(os.environ, CODEX_HOME=str(base / "profile"))
    def ask(prompt):
        event = dict(cwd=str(root), hook_event_name="UserPromptSubmit", session_id="plan-test", prompt=prompt)
        out = subprocess.check_output([sys.executable, "-I", "-X", "utf8", SCRIPT, "hook"],
                                      input=json.dumps(event).encode(), env=env, cwd=root)
        return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out.strip() else ""

    todo = root / "TASKS.md"
    todo.write_text("# Issue 17\nЧто дальше: fix selection\n", encoding="utf-8-sig")
    assert not ask("Что дальше?")
    ask("Включи хуки LoomHarness")
    out = ask("Что дальше по планам перевода?")
    assert "TASKS.md\n1 | # Issue 17\n2 | Что дальше: fix selection" in out
    todo.write_text("# Issue 21\nСделано: selection works", encoding="utf8")
    out = ask("Что уже сделано?")
    assert "selection works" in out and "Issue 17" not in out
    for text in ("Исправь ошибку и скажи, что дальше?", "Какой статус сервера?",
                 "продолжай", '"Что дальше?"', "Что дальше и исправь?", "Что дальше?\nСделай сборку"):
        assert not ask(text), text
    (root / "AGENT_STOP").touch()
    assert "TASKS.md" not in ask("Project status")
    (root / "AGENT_STOP").unlink()
    (root / ".codex").mkdir()
    config = root / ".codex/harness-plan.json"
    counts = {"file": "example.tsv", "column": "status", "values": ["obtained", "planned"]}
    config.write_text(json.dumps({"documents": ["TASKS.md", "../outside.md"], "counts": counts}))
    (base / "outside.md").write_text("private outside", encoding="utf8")
    (root / "example.tsv").write_text("name\tstatus\nA\tobtained\nB\tin progress\n", encoding="utf8")
    out = ask("What's next?")
    assert "obtained = 1" in out and "planned = 0" in out and "other = 1" in out
    assert "private outside" not in out
    (root / "example.tsv").write_text("name\tstatus\nA\tobtained\nmalformed\n", encoding="utf8")
    out = ask("Что дальше?")
    assert "example.tsv" not in out and "count unavailable" in out
    (root / "План.txt").write_text("Дальше согласовать перевод договора.", encoding="utf8")
    counts.update(file="requests.csv", delimiter=";")
    config.write_text(json.dumps({"documents":["План.txt"], "counts":counts}))
    table = root / "requests.csv"
    table.write_text('name;status\n"first; entry";obtained\n"two\nlines";planned\n', encoding="utf-8-sig")
    out = ask("Что дальше?")
    assert "План.txt" in out and "rows 2" in out and "obtained = 1" in out and "planned = 1" in out
    config.unlink()
    assert "Дальше согласовать перевод договора." in ask("Что дальше?")
    (root / "План.txt").unlink()
    nested = root / "child"
    (nested / ".git").mkdir(parents=True)
    (nested / "PLAN.md").write_text("child plan", encoding="utf8")
    config.write_text(json.dumps({"documents": ["../outside.md", "child/PLAN.md", "alias.md"]}))
    try:
        (root / "alias.md").symlink_to(base / "outside.md")
    except OSError:
        pass  # Windows can require elevated symlink privileges.
    assert not ask("Что дальше?")
    config.unlink()
    todo.write_text("# Plan\n" + "- [x] filler\n" * 220 + "# Release\n- [ ] publish R7", encoding="utf8")
    out = ask("Next steps")
    assert "222 | # Release" in out and "223 | - [ ] publish R7" in out
    assert len(out) < 3000
    ask("Отключи хуки LoomHarness")
    assert not ask("Что дальше?")
print("PASS: prompt routing, fresh lines, complete counts, project boundaries, budget, stop and opt-out")
