"""Exercise build recipes, recorded evidence and optional Java navigation through the public CLI."""

import hashlib
from importlib.util import find_spec
import json
from pathlib import Path
import subprocess
import sys


def run_checks(root, tool):
    def invoke(*args, no_site=False):
        mode = ["-I", "-S"] if no_site else ["-I"]
        return subprocess.run([sys.executable, *mode, "-X", "utf8", str(tool), *args], cwd=root,
                              capture_output=True, encoding="utf-8", timeout=20)

    def check(code, *args):
        result = invoke("build-check", *args)
        assert result.returncode == code and not result.stderr, result
        return json.loads(result.stdout)

    artifact = root / "build output.bin"
    artifact.write_bytes(b"candidate")
    ref = hashlib.sha256(artifact.read_bytes()).hexdigest()
    other = "0"*64
    report = root / "index.json"
    build = root / "build.json"
    build.write_text(json.dumps({"candidateSha256": ref, "runtimeSha256": ref}))
    ok = dict(jar=ref, label="smoke", exit=0, passes=2)
    def index(*rows):
        report.write_text(json.dumps({"releaseJar": ref, "runs": rows}))
    index(ok, dict(ok, jar=other, exit=1, errors=["stale failure"]))
    result = check(0, report, "--build-report", build, "--require", "smoke")
    assert result["runs"]["passed"] == 1 and result["other_revision_runs"] == 1 and result["passed_assertions"] == 2
    assert check(2, report, "--revision", other)["status"] == "incomplete"
    assert check(2, report, "--artifact", artifact, "--require", "missing")["runs"]["unverified"] == 1
    build.write_text(json.dumps({"candidateSha256": ref, "runtimeSha256": other}))
    assert "different" in check(2, report, "--build-report", build)["first_issue"]["message"]
    index(dict(ok, exit=None), dict(ok, status="skipped"), dict(ok, exit=1, errors=["first failure"], log="run.log"))
    result = check(1, report, "--artifact", artifact)
    assert result["runs"] == dict(passed=0, failed=1, skipped=1, unverified=1)
    assert result["first_issue"] == {"run":"smoke", "message":"first failure", "log":str(root/"run.log")}
    index(dict(ok, assertions=["PASS one", "SKIP offline"]))
    assert check(2, report, "--revision", ref)["runs"]["skipped"] == 1
    index(dict(ok, passes=0), dict(label="no identity", exit=0, passes=5))
    assert check(2, report, "--revision", ref)["runs"]["unverified"] == 2
    index(dict(ok, jar=other))
    assert check(2, report, "--revision", ref)["first_issue"]["message"] == "no runs for this revision"

    recipe = root / "check.json"
    build_args = [sys.executable, "-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(b'candidate')",
                  "{artifact}"]
    script = ("import json,pathlib,sys; json.dump({'revision':sys.argv[2],'runs':[{'revision':sys.argv[2],"
              "'label':'smoke','exit':0,'status':'passed'}]},pathlib.Path(sys.argv[1]).open('w'))")
    test = [sys.executable, "-c", script, "{index}", "{revision}", "{artifact}"]
    def run(build, tests, code):
        recipe.write_text(json.dumps({"artifact": artifact.name, "build": build, "test": tests}))
        return check(code, "--recipe", recipe, "--require", "smoke")
    first = run(build_args, test, 0)
    assert first["revision"] == ref and Path(first["report"]).is_file()
    assert (Path(first["logs"])/"build.log").is_file() and (Path(first["logs"])/"test.log").is_file()
    absent = run(build_args, [sys.executable, "-c", "pass", "{index}"], 2)
    assert first["logs"] != absent["logs"]
    broken = run([sys.executable, "-c", "print('error: compiler failed'); raise SystemExit(3)"], test, 1)
    assert broken["first_issue"]["exit"] == 3 and broken["first_issue"]["message"] == "error: compiler failed"
    assert not (Path(broken["logs"])/"test.log").exists()
    missing_exe = str(root / "missing-build-tool")
    broken = run([missing_exe], test, 1)
    assert broken["first_issue"]["stage"] == "build" and broken["first_issue"]["exit"] is None
    assert missing_exe in Path(broken["first_issue"]["log"]).read_text(encoding="utf-8")
    broken = run(build_args, [missing_exe, "{index}"], 1)
    assert broken["first_issue"]["stage"] == "test" and broken["revision"] == ref
    assert run(build_args, [*test[:2], script + "; raise SystemExit(5)", *test[3:]], 1)["first_issue"]["exit"] == 5
    changed = run(build_args, [*test[:2], script+"; pathlib.Path(sys.argv[3]).write_bytes(b'changed')", *test[3:]], 2)
    assert "changed during validation" in changed["first_issue"]["message"]

    java = root / "Store.java"
    java.write_text('''class Store {
        // void find(int fake) {}
        @Deprecated String find() { return "braces }"; }
        int find(int n) { return n; }
        class Nested { int find() { return -1; } }
        Object anon = new Object() { int find() { return 2; } };
    }''')
    fallback = invoke("symbol", java, "Store.find", no_site=True)
    assert fallback.returncode and "tree-sitter" in fallback.stderr and not fallback.stdout
    if find_spec("tree_sitter") and find_spec("tree_sitter_java"):
        result = invoke("symbol", java, "Store.find")
        assert result.returncode == 0 and result.stdout.count(str(java)) == 2, result
        assert "@Deprecated" in result.stdout and "return n;" in result.stdout and "return -1;" not in result.stdout
        assert invoke("symbol", java, "find").stdout.count(str(java)) == 4
        assert invoke("symbol", java, "Store.Nested.find").stdout.count(str(java)) == 1
        assert invoke("symbol", java, "absent").returncode
        java.write_text("class Store { void find( }")
        assert "parse error" in invoke("symbol", java, "find").stderr
