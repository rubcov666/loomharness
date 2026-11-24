"""Measure text returned by LoomHarness on fixed, generated inputs. No model calls."""

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from tempfile import TemporaryDirectory

import tiktoken

from compare_tools import log_task, map_task

SCRIPTS = Path(__file__).resolve().parents[1] / "loomharness" / "scripts"
ENCODER = tiktoken.get_encoding("o200k_base")


def command(project, script, *args, env=None):
    return subprocess.check_output(
        [sys.executable, "-I", "-X", "utf8", str(SCRIPTS / script), *args],
        cwd=project, env=env, encoding="utf8", stderr=subprocess.STDOUT, timeout=30,
    )


def usage_reports(project):
    profile = project / "profile"
    profile.mkdir()
    start, end = "2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z"
    participants = ["coordinator", "parser", "tests", "review"]
    with closing(sqlite3.connect(profile / "state_5.sqlite")) as db, db:
        db.execute("CREATE TABLE threads(id TEXT, rollout_path TEXT)")
        db.execute("CREATE TABLE thread_spawn_edges(parent_thread_id TEXT, child_thread_id TEXT)")
        for name in participants:
            log = profile / (name + ".jsonl")
            usage = {"input_tokens": 100, "cached_input_tokens": 60, "output_tokens": 20}
            payload = {"thread_id": name, "response_id": name, "usage": usage}
            log.write_text(json.dumps({"type": "token_usage_record", "timestamp": start,
                                       "payload": payload}) + "\n", encoding="utf8")
            db.execute("INSERT INTO threads VALUES (?,?)", (name, str(log)))
        db.executemany("INSERT INTO thread_spawn_edges VALUES (?,?)",
                       [(participants[0], name) for name in participants[1:]])
    env = dict(os.environ, CODEX_HOME=str(profile))
    env.pop("LOOMHARNESS_HOME", None)
    args = ("usage", "--thread", participants[0], "--since", start, "--until", end)
    full = command(project, "team.py", *args, env=env)
    short = command(project, "team.py", *args, "--summary", env=env)
    f, s = json.loads(full), json.loads(short)
    assert len(f.pop("participants")) == s.pop("participant_count") == 4
    assert f == s and s["complete"] and s["measured"]["responses"] == 4
    return full, short


def measure(project):
    _, raw_logs, summaries, _ = log_task(project)
    for text, expected in zip(summaries, ("exit=0", "test_tax_rounding", "test_refund", "Ran 0 tests", "FATAL: close")):
        assert expected in text, expected
    if "LEDGER_72" not in summaries[-1]:
        summaries.append(command(project, "view.py", "E.log", "--find", "reason_code"))
    assert "LEDGER_72" in summaries[-1]
    yield "Classify five test logs", "\n".join(raw_logs), "\n".join(summaries)

    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    _, grep, locations, _ = map_task(project)
    assert "pricing.py:1" in grep[0] and "pricing.py:1" in locations[0]
    yield "Locate a definition", grep[0], locations[0]

    rows = [{"id": i, "checks": "ok", "headSha": hashlib.sha1(str(i).encode()).hexdigest(),
             "createdAt": "2026-01-01T00:00:00Z", "url": f"https://example.invalid/jobs/{i}",
             "note": {}} for i in range(60)]
    rows[42].update(checks="failed", note={"reason": "deadline"})
    (project / "jobs.json").write_text(json.dumps(rows, separators=(",", ":")), encoding="utf8")
    full = command(project, "view.py", "jobs.json")
    selected = command(project, "view.py", "jobs.json", "--fields", "id", "checks")
    kept = [{k: row[k] for k in ("id", "checks", "note")} for row in rows]
    assert json.loads(full) == rows and json.loads(selected.split("\n", 1)[1]) == kept
    yield "Read status for 60 jobs", full, selected

    lines = [f"step={i:03} state={'retry' if i % 10 == 0 else 'ok'}" for i in range(240)]
    (project / "events.log").write_text("\n".join(lines) + "\n", encoding="utf8")
    excerpts = command(project, "view.py", "events.log", "--find", "state=retry")
    count = command(project, "view.py", "events.log", "--find", "state=retry", "--count")
    assert count.strip().endswith(": 24.") and excerpts.startswith(count)
    yield "Count 24 matching lines", excerpts, count

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    def timestamp(minute):
        return (start + timedelta(minutes=minute)).isoformat()
    series = {"windows": [{"start": timestamp(0), "end": timestamp(120), "expected": 120}],
              "samples": [{"at": timestamp(i), "value": i % 20} for i in range(120)]}
    (project / "series.json").write_text(json.dumps(series), encoding="utf8")
    args = ("series.json", "--at-least", "15")
    full = command(project, "metrics.py", *args)
    short = command(project, "metrics.py", *args, "--summary")
    f, s = json.loads(full), json.loads(short)
    assert len(f.pop("alerts")) == s.pop("alert_count") == 30
    assert len(f.pop("outside_alerts")) == s.pop("outside_alert_count") == 0 and f == s
    yield "Count alerts in 120 samples", full, short

    yield "Read totals for four agents", *usage_reports(project)

    raw = "OK\n"
    (project / "short.log").write_text(raw, encoding="utf8")
    numbered = command(project, "view.py", "short.log", "--start", "1", "--lines", "1")
    assert numbered.strip() == "1 | OK"
    yield "Read one short line", raw, numbered


def main():
    with TemporaryDirectory() as temp:
        print("| Task | Baseline tokens | LoomHarness tokens | Change |")
        print("|---|---:|---:|---:|")
        for task, baseline, result in measure(Path(temp).resolve()):
            before, after = (len(ENCODER.encode_ordinary(text)) for text in (baseline, result))
            print(f"| {task} | {before:,} | {after:,} | {(after - before) / before:+.1%} |")


if __name__ == "__main__":
    main()
