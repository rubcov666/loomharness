"""Exercise saved model choices and the coordinator setup prompts."""

import json
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from verify_team import add, cli


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
    assert choice("--preset", "manual", "--apply")["status"] == "choose-models"
    assert "model_choice" not in cli(root, "lead", "intake")
    preview = choice("--preset", "balanced")
    assert preview["status"] == "ready" and preview["policy"]["routine"]["model"] == "routine"
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


if __name__ == "__main__":
    with TemporaryDirectory() as temp:
        root = Path(temp) / "project"
        root.mkdir()
        verify(root)
    print("OK: four model presets, first use, repair, worker routing, reviewer and retained preferences")
