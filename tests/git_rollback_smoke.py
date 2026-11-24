"""Git-path rollback smoke for self_update against a local bare repo.

Run: python tests/git_rollback_smoke.py
Exits 0 and prints ROLLBACK-SMOKE-OK on success.
"""

import os
from pathlib import Path
import shutil
import subprocess
import sys

BASE = Path(__file__).resolve().parents[1] / "loomharness"
WORK_SCRIPTS = BASE / "scripts"
TMP = Path(__file__).resolve().parents[1] / "tests" / ".tmp" / ("git-smoke-" + str(os.getpid()))


def git(args, cwd):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"git {' '.join(args)} -> {result.stderr.strip()}")
    return result.stdout


def main():
    shutil.rmtree(TMP, ignore_errors=True)
    (TMP / "home").mkdir(parents=True)
    remote = TMP / "remote.git"
    git(["init", "-b", "main", "--bare", str(remote)], TMP)

    def seed(version, message):
        s = TMP / message
        git(["clone", str(remote), str(s)], TMP)

        def ignore(directory, names):
            return {n for n in names if n == "__pycache__"}

        for item in BASE.iterdir():
            if item.name in (".git", "tests", "__pycache__"):
                continue
            dest = s / item.name
            if item.is_dir():
                shutil.copytree(item, dest, ignore=ignore, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dest)
        (s / "VERSION").write_text(version + "\n", encoding="utf8")
        git(["add", "-A"], s)
        git(["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", message], s)
        git(["push", "-u", "origin", "main"], s)
        return s

    seed("1.0.0", "v1")
    install = TMP / "install"
    git(["clone", str(remote), str(install)], TMP)
    seed("2.0.0", "v2")

    os.environ["CODEX_HOME"] = str(TMP / "home")
    # Модули грузим из install-копии: skill_root() указывает на неё.
    sys.path.insert(0, str(install / "scripts"))
    import hk
    hk.load("self_update")
    su = sys.modules["hk_self_update"]
    assert Path(su.__file__).resolve().is_relative_to(install.resolve()), su.__file__

    out = []
    out.append(su.main(types.SimpleNamespace(set_origin=str(remote), branch=None,
                                             force_check=False, apply=False, rollback=False)))
    cfg = su.check(force=True)
    assert cfg.get("remote_head"), cfg
    head_v1 = su.run_git(["-C", str(install), "rev-parse", "HEAD"]).strip()
    assert su.main(types.SimpleNamespace(set_origin=None, branch=None, force_check=False,
                                         apply=True, rollback=False)) or True
    head_v2 = su.run_git(["-C", str(install), "rev-parse", "HEAD"]).strip()
    assert head_v1 != head_v2, "apply did not move HEAD"
    assert (install / "VERSION").read_text().strip() == "2.0.0"
    print("apply: HEAD", head_v1[:8], "->", head_v2[:8], "| VERSION 2.0.0 OK")
    print("rollback:", su.rollback())
    head_back = su.run_git(["-C", str(install), "rev-parse", "HEAD"]).strip()
    assert head_back == head_v1, f"rollback HEAD {head_back[:8]} != {head_v1[:8]}"
    assert (install / "VERSION").read_text().strip() == "1.0.0"
    print("rollback: HEAD", head_back[:8], "| VERSION 1.0.0 OK")
    shutil.rmtree(TMP, ignore_errors=True)
    print("ROLLBACK-SMOKE-OK")


import types  # noqa: E402

if __name__ == "__main__":
    main()
