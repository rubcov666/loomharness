"""Exercise the SSH CLI and its real remote bootstrap, replacing only the network hop."""

from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "loomharness/scripts/ssh_windows.py"
main = runpy.run_path(str(SCRIPT))["main"]
local_run = subprocess.run


def exercise(root):
    scripts = root / "O'Brien $kit"
    scripts.mkdir()
    (scripts / "team.py").write_text(
        "import json, os, sys\nfrom pathlib import Path\n"
        "with Path('calls').open('a', encoding='utf8') as out:\n"
        "    print(json.dumps([os.getenv('CODEX_THREAD_ID'), *sys.argv[1:]]), file=out)\n"
        "sys.exit(int(sys.argv[1]))\n", encoding="utf8")
    calls = root / "calls"
    transport_status = None

    def fake_ssh(argv, *, input):
        assert argv == ["ssh", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                        "-F", "profile", "--", "fixture", "python -I -X utf8 -"]
        if transport_status is not None:
            return subprocess.CompletedProcess(argv, transport_status)
        return local_run([sys.executable, "-I", "-X", "utf8", "-"], input=input,
                         env=dict(os.environ, CODEX_THREAD_ID="unrelated-remote-session"), capture_output=True)

    def invoke(*args, steps=None, thread="caller"):
        argv = [str(SCRIPT), "--project", str(root), "--scripts", str(scripts), "--ssh-config", "profile"]
        if steps is not None:
            argv.extend(["--steps", "-"])
        with patch.object(sys, "argv", [*argv, "fixture", *args]), patch("subprocess.run", fake_ssh), \
                patch.object(sys, "stdin", io.StringIO(json.dumps(steps))), patch.dict(os.environ):
            if thread:
                os.environ["CODEX_THREAD_ID"] = thread
            else:
                os.environ.pop("CODEX_THREAD_ID", None)
            return main()

    literal = "текст ' \" $(echo) & | %USERPROFILE%\nline"
    assert invoke(steps=[["team", "0", literal], ["team", "37"], ["team", "0", "unused"]]) == 37
    assert invoke("team", "0", "", thread=None) == 0
    assert list(map(json.loads, calls.read_text(encoding="utf8").splitlines())) == [
        ["caller", "0", literal], ["caller", "37"], [None, "0", ""]]
    if os.name == "nt":
        assert invoke("team", "256") == 1
    transport_status = 255
    assert invoke("team", "0") == 255
    for invalid in ([], ["team"], [["../team"]], [["team", None]]):
        with redirect_stderr(io.StringIO()):
            try:
                invoke(steps=invalid)
            except SystemExit as error:
                assert error.code == 2
            else:
                raise AssertionError(invalid)


if __name__ == "__main__":
    with TemporaryDirectory() as tmp:
        exercise(Path(tmp).resolve())
    print("PASS: SSH argv, session forwarding, first failure and transport status")
