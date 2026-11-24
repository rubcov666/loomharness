"""Run required LoomHarness steps on a Windows OpenSSH host with its Python."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


TOOLS = {"harness", "team", "context", "plan", "view", "history", "javac_cache", "metrics"}

# Only this fixed command reaches the remote login shell. The request is data
# inside a Python program sent on stdin, independent of cmd/PowerShell quoting.
REMOTE = """
import os
from pathlib import Path
import subprocess
import sys

def run(project, scripts, thread, steps):
    try:
        if not Path(project).is_absolute() or (scripts and not Path(scripts).is_absolute()):
            raise ValueError("project and scripts must be absolute paths on the remote host")
        os.chdir(project)
        base = Path(scripts) if scripts else Path.home() / ".agents/skills/loomharness/scripts"
        if thread:
            os.environ["CODEX_THREAD_ID"] = thread
        else:
            os.environ.pop("CODEX_THREAD_ID", None)
        for index, (tool, *args) in enumerate(steps, 1):
            child = subprocess.run([sys.executable, "-I", "-X", "utf8", str(base / (tool + ".py")), *args],
                                   stdin=subprocess.DEVNULL)
            code = child.returncode
            if code:
                print(f"SSH step {index} ({tool}) exited with {code}", file=sys.stderr)
                if code < 0:
                    code = 128 - code
                return code if code <= 255 else 1
        return 0
    except (OSError, ValueError) as error:
        print(f"SSH setup: {error}", file=sys.stderr)
        return 1
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="absolute project path on the remote host")
    parser.add_argument("--scripts", help="absolute directory of remote LoomHarness scripts")
    parser.add_argument("--ssh-config", help="OpenSSH config file, including host aliases and identity settings")
    parser.add_argument("--steps", metavar="FILE", help="JSON array of tool argv arrays, or - for stdin")
    parser.add_argument("host")
    parser.add_argument("argv", nargs=argparse.REMAINDER, help="tool and its arguments")
    args = parser.parse_args()
    if not args.host or args.host.startswith("-") or any(c.isspace() for c in args.host):
        parser.error("invalid SSH destination")
    if args.steps and args.argv:
        parser.error("choose a single command or --steps")
    steps = [args.argv]
    if args.steps:
        raw = sys.stdin.read() if args.steps == "-" else Path(args.steps).read_text(encoding="utf-8-sig")
        steps = json.loads(raw)
    if not isinstance(steps, list) or not steps:
        parser.error("expected a non-empty array of steps")
    for step in steps:
        if (not isinstance(step, list) or not step or
                not all(isinstance(arg, str) and "\0" not in arg for arg in step) or step[0] not in TOOLS):
            parser.error("each step must be an argv array starting with a LoomHarness tool")
    request = (args.project, args.scripts, os.environ.get("CODEX_THREAD_ID"), steps)
    program = REMOTE + "\nsys.exit(run(*" + ascii(request) + "))\n"
    ssh = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes"]
    if args.ssh_config:
        ssh.extend(["-F", args.ssh_config])
    result = subprocess.run([*ssh, "--", args.host, "python -I -X utf8 -"], input=program.encode("ascii"))
    return result.returncode if result.returncode >= 0 else 128 - result.returncode


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError) as error:
        sys.exit(str(error))
