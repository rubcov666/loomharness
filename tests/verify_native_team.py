"""Offline check for named native roles and the existing board protocol."""

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


TEAM = Path(__file__).resolve().parents[1] / "loomharness" / "scripts" / "team.py"


def main():
    with TemporaryDirectory() as scratch:
        project = Path(scratch) / "project"
        project.mkdir()
        env = dict(os.environ, CODEX_HOME=str(Path(scratch) / "profile"))

        def call(*args, actor="lead", error=None, stdin=None, raw=False):
            command = [sys.executable, "-I", "-B", "-X", "utf8", str(TEAM), "native", "--actor", actor]
            result = subprocess.run(command + list(map(str, args)), cwd=project, env=env,
                                    encoding="utf-8", input=stdin, capture_output=True, timeout=15)
            if error:
                assert result.returncode and error in result.stderr, (args, result.stderr)
            else:
                assert result.returncode == 0, (args, result.stderr)
                return result.stdout if raw else json.loads(result.stdout)

        def card(area):
            return call("add", "--role", area, "--title", area, "--scope", area, "--criterion", "Evidence matches",
                        "--resource", area, "--hours", "0.5")["id"]

        def submit(task, ticket, actor, artifact=None, quick=True):
            call("claim", task, ticket, actor=actor)
            if artifact is None:
                artifact = project / f"report-{task}.txt"
                artifact.write_text(f"Evidence for {task}", encoding="utf-8")
            revision = sha256(artifact.read_bytes()).hexdigest()
            flags = ["--quick-review"] if quick else []
            call("submit", task, ticket, "Checked", "--revision", revision, "--artifact", artifact.name,
                 *flags, actor=actor)
            return revision

        call("init", "--goal", "Make the documentation usable", "--slots", 3,
             "--simple-model", "example-worker", "--strong-model", "example-reviewer")
        call("organize", "-", "--slots", 3, "--git", "none", stdin=json.dumps({
            "navigation": "Own navigation", "style": "Own visual style", "docs": "Own documentation",
        }))
        first, second, testing = map(card, ("navigation", "style", "docs"))
        route = call("dispatch", first, "--agent-role", "designer", "--available", "none")
        assert route["taskName"] == "designer" and route["operation"] == "spawn_agent"
        designer = "/root/designer"
        ticket = route["brief"]["ticket"]
        call("dispatch", second, "--agent-role", "designer", "--available", "none", error="already assigned")
        call("bind", first, ticket, "/root/designer_23", error="exact name")
        call("bind", first, ticket, designer)
        test = call("dispatch", testing, "--agent-role", "tester", "--available", "none")
        assert test["taskName"] == "tester"
        call("dispatch", second, "--agent-role", "designer", "--available", "none",
             "--third-slot", "Separate resource requested", error="already assigned")
        assert call("status")["slots"]["active"] == 2
        first_revision = submit(first, ticket, designer)
        continued = call("dispatch", second, "--agent-role", "designer", "--available", designer)
        assert continued["operation"] == "send_input" and continued["threadId"] == designer
        new_ticket = continued["brief"]["ticket"]
        assert ticket != new_ticket
        call("claim", second, ticket, actor=designer, error="invalid assignment")
        opened = call("review-next", "--third-slot", "Review unblocks the navigation milestone")
        assert call("status")["slots"]["active"] == 3
        second_revision = submit(second, new_ticket, designer)
        call("release", testing, "Unused test reservation")

        job = opened["brief"]
        assert opened["taskName"] == "reviewer" and job["revision"] == first_revision
        call("job-bind", job["job"], job["ticket"], designer, error="independent")
        reviewer = "/root/reviewer"
        status = call("status", first, second)
        with ThreadPoolExecutor(1) as pool:
            watch = pool.submit(call, "status", first, second, "--after", status["cursor"], "--wait", 4)
            call("job-bind", job["job"], job["ticket"], reviewer)
            assert watch.result()["jobs"][0][-1] == reviewer
        call("job-claim", job["job"], job["ticket"], actor=reviewer)
        assert call("review-next", "--available", "none")["operation"] == "wait"
        call("review", second, "--expect-revision", second_revision, "--available", "none", error="already assigned")
        call("review-test", job["job"], job["ticket"], first, "--", sys.executable, "-c", "print('PASS')",
             actor=reviewer, raw=True)
        call("verdict", job["job"], job["ticket"], "approve", "Evidence matches", "--revision", first_revision,
             actor=reviewer)
        next_job = call("review-next", "--available", reviewer)
        assert next_job["operation"] == "send_input" and next_job["threadId"] == reviewer
        assert next_job["brief"]["revision"] == second_revision
        job = next_job["brief"]
        call("job-claim", job["job"], job["ticket"], actor=reviewer)
        call("verdict", job["job"], job["ticket"], "approve", "Evidence matches", "--revision", second_revision,
             actor=reviewer)

        candidate = project / "shared-candidate.txt"
        candidate.write_text("The same candidate has two acceptance criteria", encoding="utf-8")
        related = [card("navigation"), card("style")]
        for task in related:
            assignment = call("dispatch", task, "--agent-role", "designer", "--available", designer)["brief"]
            revision = submit(task, assignment["ticket"], designer, candidate, quick=False)
        grouped = call("review-next", "--available", reviewer)
        assert grouped["operation"] == "send_input" and grouped["threadId"] == reviewer
        job = grouped["brief"]
        assert [item["id"] for item in job["cards"]] == related
        assert all(item["revision"] == revision for item in job["cards"])
        call("job-claim", job["job"], job["ticket"], actor=reviewer)
        verdict = call("verdicts", job["job"], job["ticket"], "-", actor=reviewer, stdin=json.dumps([
            {"task": related[0], "revision": revision, "decision": "approve", "note": "Navigation matches"},
            {"task": related[1], "revision": revision, "decision": "changes", "note": "Style evidence is missing"},
        ]))
        assert verdict["state"] == "mixed"
        assert call("show", related[0], "--brief")["state"] == "accepted"
        assert call("show", related[1], "--brief")["state"] == "open"
        print("PASS: named roles, scoped slots, repeat assignments, event status, shared-revision batching and separate verdicts")


if __name__ == "__main__":
    main()
