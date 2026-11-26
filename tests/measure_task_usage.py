"""Compare real Codex token usage on two generated tasks with and without LoomHarness."""

import argparse
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

SCRIPTS = Path(__file__).resolve().parents[1] / "loomharness/scripts"
BEGIN, END = 350, 780
USAGE_KEYS = ("input_tokens", "cached_input_tokens", "output_tokens")
# OpenAI API Standard, USD per million, snapshot of 2026-11-26.
# https://developers.openai.com/api/docs/pricing
API_RATES = {
    "gpt-6.2-sol": (Decimal(2), Decimal("0.10"), Decimal(10)),
    "gpt-6.2-astra": (Decimal(8), Decimal("0.40"), Decimal(40)),
}


def build_task(root):
    answer = {"failures": []}
    paths = []
    for index in range(3):
        name = f"build-{index + 1}.log"
        paths.append(name)
        rows = [
            f"progress target=module-{i % 53:02} step={i:04} action=compile artifact=intermediate-cache"
            for i in range(470)
        ]
        if index != 1:
            line = 95 + index * 96
            code = ("STAMP_MISMATCH", "UNUSED", "MISSING_OUTPUT")[index]
            rows[line - 1] = f"failure step=assemble reason_code={code}"
            answer["failures"].append({"file": name, "line": line, "code": code})
        (root / name).write_text("\n".join(rows) + "\n", encoding="utf8")
    prompt = (
        "Read build-1.log, build-2.log and build-3.log. Find every line containing reason_code=."
        " Return one JSON object only with failures, an array ordered by file."
        " Each entry has file (basename), line (one-based), and code (the value after reason_code=)."
    )
    return prompt, answer


def ledger_task(root):
    lines = ["event\tservice\tstatus\tseconds"]
    evidence, total, matched = [], 0, 0
    for i in range(1800):
        service, status, seconds = (
            "billing" if i % 3 else "identity",
            "failed" if i % 41 == 2 else "done",
            1 + i % 17,
        )
        lines.append(f"{i}\t{service}\t{status}\t{seconds}")
        if BEGIN <= i < END and service == "billing" and status == "failed":
            total += seconds
            matched += 1
            if len(evidence) < 2:
                evidence.append(i + 2)
    (root / "usage.tsv").write_text("\n".join(lines) + "\n", encoding="utf8")
    prompt = (
        f"Read usage.tsv. Select billing records with status failed and {BEGIN} <= event < {END}."
        " Return one JSON object only with count, seconds_total, and first_two_lines"
        " (the first two matching one-based source line numbers including the header)."
    )
    return prompt, {"count": matched, "seconds_total": total, "first_two_lines": evidence}


def evidence(root, task, mode):
    def helper(script, *arguments, codes=(0,)):
        result = subprocess.run(
            [sys.executable, "-I", "-X", "utf8", str(SCRIPTS / script), *arguments],
            check=False,
            cwd=root,
            encoding="utf8",
            capture_output=True,
            timeout=15,
        )
        if result.returncode not in codes or result.stderr:
            raise ValueError(f"{script} failed: {result.stderr}")
        return result.stdout

    if task is build_task:
        blocks = []
        for log in sorted(root.glob("build-*.log")):
            text = (
                log.read_text(encoding="utf8")
                if mode == "without"
                else helper("view.py", log.name, "--find", "reason_code=", codes=(0, 1))
            )
            blocks.append(f"File: {log.name}\n{text}")
        return "\n".join(blocks)
    if mode == "without":
        return (root / "usage.tsv").read_text(encoding="utf8")
    query = {
        "where": {"service": "billing", "status": "failed"},
        "gte": {"event": BEGIN},
        "lt": {"event": END},
        "sums": ["seconds"],
        "fields": ["event", "seconds"],
        "limit": 2,
    }
    return helper("records.py", "usage.tsv", "--query", json.dumps(query))


def run(args, task, mode):
    with TemporaryDirectory(prefix="loom-task-") as name:
        root = Path(name)
        prompt, expected = task(root)
        supplied = evidence(root, task, mode)
        cmd = [
            args.codex,
            "exec",
            "--ignore-user-config",
            "--disable",
            "hooks",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--json",
            "--color",
            "never",
            "-m",
            args.model,
            "-c",
            f'model_reasoning_effort="{args.effort}"',
            "-C",
            str(root),
            "-",
        ]
        start = monotonic()
        prompt += "\nUse the attached file evidence. Return the answer directly.\n\n" + supplied
        proc = subprocess.run(
            cmd, check=False, input=prompt + "\n", encoding="utf8", capture_output=True, timeout=150
        )
        events = [json.loads(line) for line in proc.stdout.splitlines() if line.startswith("{")]
        replies = [
            event["item"]["text"]
            for event in events
            if event["type"] == "item.completed" and event.get("item", {}).get("type") == "agent_message"
        ]
        usage = [event["usage"] for event in events if event["type"] == "turn.completed"]
        if proc.returncode or not replies or not usage:
            raise RuntimeError(f"{task.__name__}/{mode} failed: {proc.stderr[-1000:]}")
        answer = replies[-1].strip().removeprefix("```json").removesuffix("```").strip()
        try:
            accepted = json.loads(answer) == expected
        except json.JSONDecodeError:
            accepted = False
        counters = {key: sum(event[key] for event in usage) for key in USAGE_KEYS}
        counters["total_tokens"] = counters["input_tokens"] + counters["output_tokens"]
        return {**counters, "seconds": round(monotonic() - start, 2), "accepted": accepted}


def report(paths):
    print("| Model | Mode | Accepted | Input | Cached input | Output | Total | Estimated API USD |")
    print("|---|---|---:|---:|---:|---:|---:|---:|")
    savings = []
    for path in paths:
        data = json.loads(path.read_text(encoding="utf8"))
        model, runs = data["model"], data["runs"]
        rows = {}
        for mode in ("without", "with"):
            arm = [row for row in runs if row["mode"] == mode]
            if not arm:
                raise ValueError(f"{path.name}: no runs {mode} LoomHarness")
            accepted = sum(row["accepted"] for row in arm)
            input_tokens, cached, output = (sum(row[key] for row in arm) for key in USAGE_KEYS)
            total = input_tokens + output
            cost = None
            if model in API_RATES:
                price_input, price_cache, price_output = API_RATES[model]
                cost = (
                    (input_tokens - cached) * price_input + cached * price_cache + output * price_output
                ) / 1_000_000
            rows[mode] = (total, cost)
            label = f"${cost:.6f}" if cost is not None else "—"
            print(
                f"| {model} | {mode} | {accepted}/{len(arm)} | {input_tokens:,} | {cached:,} | {output:,} | {total:,} | {label} |"
            )
        before, before_cost = rows["without"]
        after, after_cost = rows["with"]
        tokens_saved = before - after
        line = f"- {model}: {tokens_saved:,} tokens saved ({tokens_saved / before:.2%})"
        if before_cost is not None:
            saved = before_cost - after_cost
            line += f", ${saved:.6f} saved ({saved / before_cost:.2%})"
        savings.append(line)
    print("\n" + "\n".join(savings))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", default="codex")
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--model")
    operation.add_argument("--report", nargs="+", type=Path, metavar="JSON")
    parser.add_argument("--effort", default="low")
    parser.add_argument("--pairs", type=int, default=1)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.report:
        report(args.report)
        return
    if args.pairs < 1:
        parser.error("--pairs must be positive")
    trials = []
    for pair in range(args.pairs):
        for index, task in enumerate((build_task, ledger_task)):
            order = ("without", "with") if (pair + index) % 2 == 0 else ("with", "without")
            for mode in order:
                trials.append({"task": task.__name__, "pair": pair + 1, "mode": mode, **run(args, task, mode)})
    version = subprocess.check_output([args.codex, "--version"], encoding="utf8").strip()
    data = (
        json.dumps({"codex": version, "model": args.model, "effort": args.effort, "runs": trials}, indent=2)
        + "\n"
    )
    if args.output:
        args.output.write_text(data, encoding="utf8")
    print(data, end="")


if __name__ == "__main__":
    main()
