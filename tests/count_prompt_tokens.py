"""Count model-facing templates from one LoomHarness checkout with identical inputs."""

import argparse
import os
from pathlib import Path
import runpy
from tempfile import TemporaryDirectory

import tiktoken


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill", nargs="?", type=Path, default=Path(__file__).resolve().parents[1] / "loomharness")
    skill = parser.parse_args().skill.resolve()
    scripts = skill / "scripts"
    encoding = tiktoken.get_encoding("o200k_base")
    with TemporaryDirectory() as tmp:
        os.environ["CODEX_HOME"] = tmp
        harness = runpy.run_path(str(scripts / "harness.py"))
        driver = runpy.run_path(str(scripts / "team_driver.py"))
        card = dict(id=3, role="dev", title="Fix import", goal="Ship it", scope="a.py", criterion="Tests pass", hours=1)
        texts = {"SKILL.md": (skill / "SKILL.md").read_text(encoding="utf8"), "Opt-in": harness["ASK"]}
        for label, team in (("SessionStart (solo)", False), ("SessionStart (team)", True)):
            hint = harness["session_hint"](None, Path(tmp), "", board=(team, None))
            # The executable and installation paths depend on the machine.
            texts[label] = hint.removesuffix(harness["view_command"]() + "\n")
        texts["Worker card"] = driver["card_prompt"](card, "project", [{"id": 1}])
        for name, text in texts.items():
            print(f"{name}\t{len(encoding.encode(text))}")


if __name__ == "__main__":
    main()
