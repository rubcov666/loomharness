"""Run LoomHarness's offline checks using the tools installed on this computer."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PYTHON_CHECKS = (
    "verify_ssh_windows.py",
    "verify_context.py",
    "verify_history.py",
    "verify_views.py",
    "verify_records.py",
    "verify_plan.py",
    "verify_team.py",
    "verify_native_team.py",
    "verify_organization.py",
    "verify_initiative.py",
    "verify_efficiency.py",
    "verify_model_choice.py",
    "verify_board_optimizations.py",
    "verify_adaptive_team.py",
    "verify_workflows.py",
    "verify_compact_io.py",
    "test_core_audit.py",
    "git_rollback_smoke.py",
    "verify_update.py",
)


def main():
    if not shutil.which("git"):
        sys.exit("Local verification requires Git on PATH.")

    python = [sys.executable, "-X", "utf8"]
    install = [*python, "tests/verify_install.py"]
    if shutil.which("codex"):
        install.append("--codex")
    else:
        print("SKIP: native Codex registration check (Codex CLI unavailable).", flush=True)

    commands = [
        install,
        [*python, "tests/verify_harness.py", "loomharness/scripts/harness.py"],
        *([*python, f"tests/{script}"] for script in PYTHON_CHECKS),
    ]
    if node := shutil.which("node"):
        commands.extend(([node, "tests/verify_tool_examples.cjs"], [node, "tests/verify_opencode.mjs"]))
    else:
        print("SKIP: JavaScript and OpenCode checks (Node.js unavailable).", flush=True)
    if shutil.which("java") and shutil.which("javac"):
        commands.append([*python, "tests/verify_javac.py"])
    else:
        print("SKIP: Java compilation checks (JDK unavailable).", flush=True)

    # Keep command-line Git configuration out of the temporary repositories.
    env = {
        key: value
        for key, value in os.environ.items()
        if key != "GIT_CONFIG_COUNT" and not key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))
    }
    env["LOOMHARNESS_REPO"] = str(REPO)
    for index, command in enumerate(commands, 1):
        script = next(argument for argument in command if argument.startswith("tests/"))
        print(f"[{index}/{len(commands)}] {script}", flush=True)
        try:
            result = subprocess.run(command, cwd=REPO, env=env, timeout=180, check=False)
        except subprocess.TimeoutExpired:
            sys.exit(f"FAIL: {script} timed out")
        if result.returncode:
            sys.exit(f"FAIL: {script} exited with code {result.returncode}")
    print(f"PASS: {len(commands)} local checks")


if __name__ == "__main__":
    main()
