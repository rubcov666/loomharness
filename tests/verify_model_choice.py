"""Exercise saved model choices and the coordinator setup prompts."""

import json
import runpy
import sys
from hashlib import sha256
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from verify_team import SCRIPTS, add, cli


def verify(root):
    catalogue = root / "models.json"
    rows = [
        {"id": "main", "default": True, "effort": "low", "efforts": ["low", "medium"]},
        {"id": "expert", "tier": "strong", "effort": "medium", "efforts": ["medium", "high"]},
        {"id": "alternate", "effort": "medium", "efforts": ["low", "medium"]},
        {"id": "routine", "tier": "fast", "effort": "low", "efforts": ["low"]},
    ]

    def write(entries):
        catalogue.write_text(json.dumps(entries), encoding="utf8")

    def choice(*args, actor="lead", fail=False):
        return cli(root, actor, "model-choice", "--lang", "ru", "--catalog", catalogue, *args, fail=fail)

    write(rows)
    menu = choice()
    assert menu["status"] == "choose-preset" and menu["recommended"] == "balanced"
    assert [entry["id"] for entry in menu["options"]] == ["current", "balanced", "strong", "manual"]
    manual_prompt = choice("--preset", "manual", "--apply")
    assert manual_prompt["status"] == "choose-models"
    assert [question["role"] for question in manual_prompt["questions"]] == [
        "coordinator",
        "worker",
        "reviewer",
    ]
    assert choice("--preset", "manual", "--coordinator", "expert")["questions"][0]["role"] == "worker"
    manual_preview = choice(
        "--preset", "manual", "--coordinator", "expert", "--worker", "main", "--reviewer", "expert"
    )
    assert manual_preview["policy"]["routine"] is None
    assert "model_choice" not in cli(root, "lead", "intake")
    preview = choice("--preset", "balanced")
    assert preview["status"] == "ready" and preview["policy"]["routine"]["model"] == "routine"
    assert [item["id"] for item in preview["confirmation"]["options"]] == ["confirm", "change"]
    assert preview["policy"]["roles"] == {
        "worker": {"model": "main", "thinking": "low"},
        "coordinator": {"model": "expert", "thinking": "high"},
        "reviewer": {"model": "expert", "thinking": "medium"},
    }
    assert "model_choice" not in cli(root, "lead", "intake")
    saved = choice("--preset", "balanced", "--apply")["policy"]
    assert cli(root, "lead", "intake")["model_choice"] == saved
    cli(root, "lead", "init", "--goal", "Validate the supplier exports", "--slots", 2, "--git", "none")
    manifest = root / "owners.json"
    manifest.write_text(json.dumps({"Data": "Source data"}), encoding="utf8")
    cli(root, "lead", "organize", manifest, "--slots", 2)
    task = add(root, "Data", "Validate importing the file", "--resource", "input")
    worker = cli(root, "lead", "dispatch", task, "--stage", "bounded", "--work-type", "extract")
    assert worker["model"] == "main"
    key = worker["brief"]["ticket"]
    cli(root, "worker", "claim", task, key)
    assert cli(root, "lead", "next-stage", task, "--stage", "diagnosis")["model"] == "expert"
    proof = root / "report.txt"
    proof.write_text("checked all values", encoding="utf8")
    digest = sha256(proof.read_bytes()).hexdigest()
    cli(
        root,
        "worker",
        "submit",
        task,
        key,
        "checked full source",
        "--artifact",
        proof.name,
        "--revision",
        digest,
    )
    review = cli(root, "lead", "review", task)
    assert review["model"] == "expert" and review["thinking"] == "medium"

    assert "only the project lead" in choice(
        "--preset", "current", "--current", "main", "--apply", actor="worker", fail=True
    )
    current = choice("--preset", "current", "--current", "main", "--current-effort", "medium", "--apply")[
        "policy"
    ]
    assert current["routine"] is None and all(
        v == {"model": "main", "thinking": "medium"} for v in current["roles"].values()
    )
    assert choice()["policy"] == current
    assert "confirmation" not in choice()
    task = add(root, "Data", "Check the reconciled export", "--resource", "reserve")
    result = cli(root, "lead", "dispatch", task, "--tier", "strong")
    assert result["model"] == "main" and result["thinking"] == "medium"
    write(rows[1:])
    unresolved = choice()
    assert unresolved["status"] == "choose-models" and [item["role"] for item in unresolved["questions"]] == [
        "current"
    ]
    assert "main" in unresolved["questions"][0]["question"]
    assert cli(root, "lead", "intake")["model_choice"] == current
    assert choice("--preset", "strong", "--strong", "expert", "--apply")["policy"]["routine"] is None
    manual = choice(
        "--preset",
        "manual",
        "--coordinator",
        "expert",
        "--worker",
        "alternate",
        "--reviewer",
        "expert",
        "--no-routine",
        "--apply",
    )
    assert manual["policy"]["routine"] is None and choice()["policy"] == manual["policy"]
    unranked = [dict(row, tier=None) for row in rows]
    write(unranked)
    assert choice("--preset", "balanced")["questions"][0]["role"] == "strong"


class Terminal(StringIO):
    def isatty(self):
        return True


def verify_terminal(root):
    root.mkdir()
    catalogue = root / "models.json"
    catalogue.write_text(
        json.dumps([{"id": "vendor/steady", "label": "Steady"}, {"id": "vendor/quick", "label": "Quick"}]),
        encoding="utf8",
    )
    team = runpy.run_path(str(SCRIPTS / "team.py"))

    def respond(data, *extra):
        args = team["build_parser"]().parse_args(
            [
                "--project",
                str(root),
                "--actor",
                "portable-lead",
                "model-choice",
                "--interactive",
                "--catalog",
                str(catalogue),
                *extra,
            ]
        )
        stdout, stderr = StringIO(), Terminal()
        with (
            patch.object(sys, "stdin", Terminal(data)),
            patch.dict("os.environ", CODEX_HOME=str(root.parent / "codex")),
        ):
            team["run"](args, args.actor_id, stdout=stdout, stderr=stderr)
        return json.loads(stdout.getvalue()), stderr.getvalue()

    for cancel in ("", ":cancel\n"):
        result, _ = respond("manual\n1\n2\n1\n" + cancel)
        assert result == {"status": "cancelled"}
        assert "model_choice" not in cli(root, "portable-lead", "intake")
    assert "requires a live terminal" in cli(root, "portable-lead", "model-choice", "--interactive", fail=True)
    result, transcript = respond(
        "4\n1\nmissing\n2\n1\nchange\nmanual\nQuick\nvendor/steady\n2\n1\n", "--lang", "en"
    )
    assert result["saved"] and "confirmation" not in result
    assert "Choose a listed option." in transcript
    assert transcript.count("Use these models for this project?") == 2
    assert result["policy"]["roles"]["coordinator"]["model"] == "vendor/quick"
    assert result["policy"]["roles"]["worker"]["model"] == "vendor/steady"
    assert cli(root, "portable-lead", "intake")["model_choice"] == result["policy"]
    assert respond("")[1] == ""
    cli(root, "portable-lead", "init", "--goal", "Review the source archive", "--git", "none")
    assert cli(root, "", "--actor", "portable-lead", "intake")["actor"] == "lead"
    no_terminal = team["execute_request"](
        {"argv": ["model-choice", "--interactive"], "actor": "portable-lead", "project": str(root)},
        team["build_parser"](),
    )
    assert not no_terminal["ok"] and "requires a live terminal" in no_terminal["error"]


if __name__ == "__main__":
    with TemporaryDirectory() as temp:
        root = Path(temp) / "project"
        root.mkdir()
        verify(root)
        verify_terminal(Path(temp) / "terminal")
    print(
        "OK: four presets, role choices, confirmation, terminal, cleanup on cancellation and retained preferences"
    )
