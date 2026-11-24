"""Tiny client for team.py serve: minimal imports for fast one-shot calls.

Usage: python hk_call.py --project ПУТЬ [--actor THREAD] КОМАНДА [АРГУМЕНТЫ...]
Requires a running serve process (python team.py serve).
"""

import json
import os
from pathlib import Path
import socket
import sys


try:
    import hk
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import hk


def call(port_file, project, actor, argv):
    try:
        port = int(port_file.read_text(encoding="utf8").strip())
    except (OSError, ValueError):
        print("no serve process; start one with: python team.py serve", file=sys.stderr)
        return 1
    request = {"argv": argv, "project": str(project), "actor": actor}
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=3) as link:
            # The inbox owns its deadline. A review test runs until its child process exits.
            link.settimeout(None if argv[:1] in (["inbox"], ["review-test"]) else 60)
            link.sendall((json.dumps(request, ensure_ascii=False) + "\n").encode("utf8"))
            with link.makefile("rb") as reader:
                response = json.loads(reader.readline())
    except (OSError, ValueError) as exc:
        print(f"serve unavailable ({exc}); inspect the board before retrying a write", file=sys.stderr)
        return 1
    if response.get("stdout"):
        sys.stdout.write(response["stdout"])
    if not response.get("ok"):
        print(response.get("error", "command failed"), file=sys.stderr)
    return 0 if response.get("ok") else 1


def main():
    args = sys.argv[1:]
    project = actor = None
    rest = []
    i = 0
    while i < len(args):
        if args[i] == "--project" and i + 1 < len(args):
            project = args[i + 1]
            i += 2
        elif args[i] == "--actor" and i + 1 < len(args):
            actor = args[i + 1]
            i += 2
        elif args[i] == "--":
            rest.extend(args[i + 1:])
            break
        else:
            rest.append(args[i])
            i += 1
    return call(hk.kit_home() / "serve.port", project or os.getcwd(), actor or os.environ.get("CODEX_THREAD_ID"), rest)


if __name__ == "__main__":
    sys.exit(main())
