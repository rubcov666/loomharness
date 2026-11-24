"""Paired synthetic tasks. Default: count evidence locally. --model also runs live Codex."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from verify_context import invoke
from verify_install import temporary_profile


def log_task(folder):
    ok = [f"test_total_{i:03} (__main__.InvoiceTests.test_total_{i:03}) ... ok" for i in range(120)]
    cases = {
        "A": (ok + ["Ran 120 tests", "OK"], 0),
        "B": (["ERROR: test_tax_rounding"] + ok + ["Ran 121 tests", "FAILED (errors=1)"], 1),
        "C": (ok + ["test_refund ... skipped 'no provider'", "OK (skipped=1)"], 0),
        "D": (["Ran 0 tests", "OK"], 0),
        "E": (ok + [f"phase {i}" for i in range(35)] + ["reason_code=LEDGER_72", "FATAL: close"], 3),
    }
    full, compact = [], []
    for case, (lines, code) in cases.items():
        text = "\n".join(lines) + "\n"
        (folder / "feed").write_bytes(text.encode())
        program = "from pathlib import Path; import sys; sys.stdout.buffer.write(Path('feed').read_bytes());"
        proc = invoke(folder, "run", "--log", case + ".log", "--", sys.executable, "-c",
                      program + f"sys.exit({code})")
        assert proc.returncode == code and (folder / (case + ".log")).read_bytes() == text.encode()
        full.append(f"{case}.log exit={code}\n{text}")
        compact.append(proc.stderr + proc.stdout)
    question = ("Classify independent logs A-E. PASS requires executed tests and no failures or skips. "
                "For a failure give the failing test name or the exact reason_code value; for a skip give "
                "the skipped test name. If details are missing, search the named .log locally for reason_code. "
                "Return JSON mapping A-E to one string each: PASS, EMPTY, FAIL <name or code>, SKIP <name>.")
    return question, full, compact, {"A": "PASS", "B": "FAIL test_tax_rounding", "C": "SKIP test_refund",
                                      "D": "EMPTY", "E": "FAIL LEDGER_72"}


def map_task(folder):
    (folder / "pricing.py").write_text("def discount_rate(total):\n    return 7 if total >= 180 else 0\n",
                                       encoding="utf8")
    for i in range(6):
        lines = ["from pricing import discount_rate", f"def quote_{i}(amount):"]
        lines.extend(f"    value_{j} = discount_rate(amount + {j})" for j in range(12))
        (folder / f"quote_{i}.py").write_text("\n".join(lines) + "\n", encoding="utf8")
    subprocess.run(["git", "add", "--", "*.py"], cwd=folder, check=True)
    hits = subprocess.check_output(["git", "grep", "-n", "-F", "discount_rate", "--", "*.py"], cwd=folder)
    locations = invoke(folder, "map", "discount_rate")
    assert locations.returncode == 0
    question = ("Locate the definition of discount_rate and read the implementation locally. "
                "Return JSON with file (relative path), line (definition), minimum (qualifying amount), "
                "and rate (integer).")
    return question, [hits.decode()], [locations.stdout], {"file": "pricing.py", "line": 1, "minimum": 180, "rate": 7}


def overflow_task(folder, *, size=100):
    # A diagnostic in the middle that no text classifier can identify as such.
    lines = [f"worker {i} processing partition" for i in range(size)]
    lines[45] = "reason_code=QUOTA_R17"
    lines[-1] = "fatal: worker stopped"
    raw = "\n".join(lines) + "\n"
    (folder / "feed").write_bytes(raw.encode())
    program = "from pathlib import Path; import sys; sys.stdout.buffer.write(Path('feed').read_bytes()); sys.exit(4)"
    proc = invoke(folder, "run", "--log", "E.log", "--", sys.executable, "-c", program)
    assert proc.returncode == 4
    assert ("REVIEW" in proc.stdout) == (size > 100)
    assert ("QUOTA_R17" in proc.stdout) == (size == 100)
    return ("Identify the failure in E.log. Look up reason_code in the full log if necessary. "
            "Return JSON with exit and reason_code.", ["E.log exit=4\n" + raw],
            [proc.stderr + proc.stdout], {"exit": 4, "reason_code": "QUOTA_R17"})


def audit_task(folder):
    profile = folder / "profile"
    profile.mkdir()
    service = folder / "service"
    service.mkdir()
    rule = "Record the audit source and date in the receipt."
    docs = {}
    for prefix, path in [("root", folder / "AGENTS.md"), ("service", service / "AGENTS.override.md")]:
        lines = [f"Validate {prefix} format {i:02} against version {i + 1} of the fixture." for i in range(48)]
        lines[5 if prefix == "root" else 21] = rule
        path.write_text("\n".join(lines) + "\n", encoding="utf8")
        docs[path.relative_to(folder).as_posix()] = path
    (service / "AGENTS.md").write_text("This file is shadowed.\n", encoding="utf8")
    (profile / "config.toml").write_text(
        '[mcp_servers.reports]\ncommand="example"\nenabled=false\n', encoding="utf8")
    out = invoke(service, "audit", env=dict(os.environ, CODEX_HOME=str(profile)))
    assert out.returncode == 0, out.stderr
    full = [f"{name}\n" + "\n".join(f"{n}: {s}" for n, s in enumerate(
            path.read_text(encoding="utf8").splitlines(), 1)) for name, path in docs.items()]
    full.append("Configured MCP servers: reports (disabled)")
    question = ("Audit the CLI instruction chain for service. Return JSON with duplicate (an ordered pair of "
                "file:line strings for the same repeated instruction, root first), and disabled (list of "
                "disabled MCP server names).")
    return question, full, [out.stdout], {"duplicate": ["AGENTS.md:6", "service/AGENTS.override.md:22"],
                                         "disabled": ["reports"]}


def review(folder, model, question, evidence, expected):
    prompt = (question + "\nUse only the provided material. For any file read use the fixture_reader "
              "MCP tool read_fixture (file, optional find). Return just JSON.\n" + evidence)
    answer = folder / "answer.json"
    answer.unlink(missing_ok=True)
    reader = Path(__file__).resolve().with_name("fixture_reader.py")
    command = [shutil.which("codex"), "exec", "--ephemeral", "--disable", "hooks", "--skip-git-repo-check",
               "--sandbox", "read-only", "-m", model, "-c", 'model_reasoning_effort="medium"',
               "-c", "mcp_servers.fixture_reader.command=" + json.dumps(Path(sys.executable).as_posix()),
               "-c", 'mcp_servers.fixture_reader.tools.read_fixture.approval_mode="approve"',
               "-c", "mcp_servers.fixture_reader.args=" + json.dumps(["-I", "-X", "utf8", reader.as_posix(),
                                                                       folder.as_posix()]),
               "--json", "-o", str(answer), "-"]
    run = subprocess.run(command, cwd=folder, input=prompt.encode(), capture_output=True, timeout=240)
    events = [json.loads(line) for line in run.stdout.splitlines() if line.strip()]
    if run.returncode or not answer.exists():
        raise RuntimeError(f"Codex exited {run.returncode}. " + str([e.get("type") for e in events[-5:]]))
    response = answer.read_text(encoding="utf8").strip()
    if response.startswith("\x60\x60\x60"):
        response = "\n".join(response.splitlines()[1:-1])
    try:
        correct = json.loads(response) == expected
    except ValueError:
        correct = False
    usage = next(e["usage"] for e in events if e.get("type") == "turn.completed")
    calls = sum(e.get("type") == "item.completed" and
                e.get("item", {}).get("type") in {"command_execution", "mcp_tool_call"} for e in events)
    if not correct:
        print("Unexpected synthetic answer:", response[:1000], flush=True)
        for event in events:
            item = event.get("item", {})
            if event.get("type") == "item.completed" and item.get("type") == "mcp_tool_call":
                print("Fixture call:", item.get("status"), str(item.get("error", ""))[:350], flush=True)
    return dict(correct=correct, calls=calls, **{k: usage[k] for k in
                ("input_tokens", "cached_input_tokens", "output_tokens")})


if __name__ == "__main__":
    import tiktoken
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help="Runs two signed-in Codex tasks per selected scenario (uses allowance).")
    parser.add_argument("--task", choices=["run", "map", "audit", "overflow", "large"])
    parser.add_argument("--mode", choices=["ordinary", "compact"], help="Repeat one arm of a comparison.")
    args = parser.parse_args()
    encoder = tiktoken.get_encoding("o200k_base")
    all_correct = True
    with temporary_profile() as base:
        for name, build in [("run", log_task), ("map", map_task), ("audit", audit_task),
                            ("overflow", overflow_task), ("large", lambda folder: overflow_task(folder, size=350))]:
            if args.task and name != args.task:
                continue
            folder = base / name
            folder.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=folder, check=True)
            question, full, compact, expected = build(folder)
            # Keep audit fixtures outside the Codex working directory, so the
            # fixture AGENTS files are not also injected into the live prompt.
            chat = folder if name != "audit" else base / "audit-chat"
            chat.mkdir(exist_ok=True)
            for mode, chunks in [("ordinary", full), ("compact", compact)]:
                if args.mode and args.mode != mode:
                    continue
                evidence = "\n".join(chunks)
                row = {"evidence_tokens": len(encoder.encode_ordinary(evidence))}
                if args.model:
                    row.update(review(chat, args.model, question, evidence, expected))
                    all_correct &= row["correct"]
                print(name, mode, json.dumps(row), flush=True)
    sys.exit(0 if all_correct else 1)
