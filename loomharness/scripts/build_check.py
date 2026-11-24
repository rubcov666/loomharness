"""Verify one build's recorded test runs or execute an explicit local recipe."""

import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile


def object_from(path):
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return value


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def sha(value):
    if not isinstance(value, str) or not re.fullmatch("[0-9a-fA-F]{64}", value):
        raise ValueError("expected a full SHA-256")
    return value.lower()


def expected_revision(args):
    if args.build_report:
        report = object_from(args.build_report)
        candidate = sha(report.get("candidateSha256"))
        if "runtimeSha256" in report and sha(report["runtimeSha256"]) != candidate:
            raise ValueError("build report has different runtime and candidate digests")
        return candidate
    if args.artifact:
        return digest(args.artifact)
    return sha(args.revision)


def run_outcome(row):
    code, status = row.get("exit"), row.get("status")
    passes = row.get("passes", 0)
    assertions = row.get("assertions", [])
    errors = row.get("errors", [])
    if (code is not None and type(code) is not int or type(passes) is not int or passes < 0
            or any(not isinstance(items, list) or not all(isinstance(s, str) for s in items)
                   for items in (assertions, errors))):
        return "unverified", "invalid test record", 0
    failures = [line for line in assertions if re.match(r"\s*(?:FAIL(?:ED|URE)?|ERROR)\b", line, re.I)]
    if errors or failures or code not in (0, None) or status == "failed":
        return "failed", next(iter(errors or failures), f"test failed (exit {code})"), 0
    if status == "skipped":
        return "skipped", "test skipped", 0
    skipped = next((s for s in assertions if re.match(r"\s*SKIP(?:PED)?\b", s, re.I)), None)
    if skipped:
        return "skipped", skipped, 0
    positives = sum(bool(re.match(r"\s*PASS(?:ED)?\b", line, re.I)) for line in assertions)
    if code == 0 and status in (None, "passed") and (passes or positives or status == "passed"):
        return "passed", None, passes if "passes" in row else positives
    return "unverified", "missing completion or positive test evidence", 0


def summarize(path, revision, required):
    report = object_from(path)
    declared = report.get("revision", report.get("releaseJar"))
    if declared is not None and sha(declared) != revision:
        raise ValueError("validation index revision differs from the selected build")
    rows = report.get("runs")
    if not isinstance(rows, list):
        raise ValueError("expected a runs array in the validation index")
    counts = dict(passed=0, failed=0, skipped=0, unverified=0)
    seen = set()
    first = {}
    assertions = other = 0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid entry in runs")
        ref = row.get("revision", row.get("jar"))
        label = str(row.get("label") or "(unnamed)")
        if ref is None:
            status, reason, passed = "unverified", "missing run revision", 0
        elif sha(ref) != revision:
            other += 1
            continue
        else:
            seen.add(label)
            status, reason, passed = run_outcome(row)
        counts[status] += 1
        assertions += passed
        if reason and status not in first:
            first[status] = {"run": label, "message": reason[:500]}
            if isinstance(row.get("log"), str) and row["log"]:
                first[status]["log"] = str((path.parent / row["log"]).resolve())
    missing = sorted(set(required) - seen)
    counts["unverified"] += len(missing)
    if missing:
        first.setdefault("unverified", {"run": missing[0], "message": "required run missing"})
    status = "failed" if counts["failed"] else "incomplete" if (
        counts["skipped"] or counts["unverified"] or not counts["passed"]) else "passed"
    result = {"status": status, "revision": revision, "runs": counts, "passed_assertions": assertions,
              "other_revision_runs": other, "report": str(path.resolve())}
    if status != "passed":
        result["first_issue"] = next((first[key] for key in ("failed", "unverified", "skipped") if key in first),
                                     {"message": "no runs for this revision"})
    return result


def command(recipe, key):
    argv = recipe.get(key)
    if not isinstance(argv, list) or not argv or not all(isinstance(s, str) and s for s in argv):
        raise ValueError(f"recipe {key} must be a nonempty argument array")
    return argv


def run_stage(argv, cwd, logfile):
    with logfile.open("xb") as output:
        try:
            return subprocess.run(argv, cwd=cwd, stdout=output, stderr=subprocess.STDOUT).returncode
        except OSError as error:
            output.write(f"Cannot start {argv[0]} ({error})\n".encode("utf-8", errors="backslashreplace"))
            return None


def stage_error(stage, code, log):
    first = None
    with log.open(encoding="utf-8", errors="replace") as lines:
        for line in lines:
            line = line.strip()
            if line:
                first = first or line[:500]
                if re.search(r"\b(?:error|fatal|exception|failed|failure)\b", line, re.I):
                    first = line[:500]
                    break
    return {"stage": stage, "exit": code, "message": first or "command failed", "log": str(log)}


def execute_recipe(path, required, result):
    root = path.resolve().parent
    recipe = object_from(path)
    build, tests = command(recipe, "build"), command(recipe, "test")
    if not any("{index}" in part for part in tests):
        raise ValueError("test arguments must include {index} for this run's fresh validation index")
    if not isinstance(recipe.get("artifact"), str) or not recipe["artifact"]:
        raise ValueError("recipe needs an artifact path")
    artifact = (root / recipe["artifact"]).resolve()
    log_root = root / ".loomharness" / "logs"
    log_root.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="build-", dir=log_root))
    index = run / "validation-index.json"
    result["logs"] = str(run)
    build_log, test_log = run / "build.log", run / "test.log"
    build = [s.replace("{artifact}", str(artifact)) for s in build]
    code = run_stage(build, root, build_log)
    if code != 0:
        result.update(status="failed", first_issue=stage_error("build", code, build_log))
        return
    revision = digest(artifact)
    result["revision"] = revision
    tests = [s.replace("{index}", str(index)).replace("{artifact}", str(artifact))
             .replace("{revision}", revision) for s in tests]
    code = run_stage(tests, root, test_log)
    if code != 0:
        result.update(status="failed", first_issue=stage_error("test", code, test_log))
    if digest(artifact) != revision:
        raise ValueError("artifact changed during validation")
    if index.exists() or code == 0:
        summary = summarize(index, revision, required)
        if code != 0:
            summary.update(status="failed", first_issue=result["first_issue"])
        result.update(summary)


def check(args):
    result = {"status": "incomplete"}
    try:
        if args.recipe:
            if args.index or args.artifact or args.build_report or args.revision:
                raise ValueError("use either --recipe or a recorded index with an independent build identity")
            execute_recipe(args.recipe, args.require, result)
        else:
            if not args.index or not (args.revision or args.artifact or args.build_report):
                raise ValueError("recorded checks need INDEX and --revision, --artifact or --build-report")
            result["report"] = str(args.index.resolve())
            result["revision"] = expected_revision(args)
            result.update(summarize(args.index, result["revision"], args.require))
    except (OSError, ValueError) as error:
        result.setdefault("first_issue", {"message": str(error)})
        if result["status"] == "failed":
            result["evidence_error"] = str(error)
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return {"passed": 0, "failed": 1, "incomplete": 2}[result["status"]]
