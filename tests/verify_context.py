"""Exercise the three context tools through their public commands with isolated fixtures."""

import os
from pathlib import Path
import subprocess
import sys
from verify_install import temporary_profile


TOOL = Path(os.environ.get("LOOMHARNESS_REPO", Path(__file__).resolve().parents[1])) / "loomharness/scripts/context.py"


def invoke(cwd, *args, env=None):
    return subprocess.run([sys.executable, "-I", "-X", "utf8", str(TOOL), *args], cwd=cwd, env=env,
                          capture_output=True, encoding="utf8", errors="replace", timeout=25)


def capture(cwd, log, payload, code=0):
    # Put diagnostics on stderr, as real runners do. Check that the full byte
    # stream survives, even for a skipped/empty run or invalid UTF-8.
    (cwd / "fixture.bin").write_bytes(payload)
    program = ("import pathlib,sys; sys.stderr.buffer.write(pathlib.Path('fixture.bin').read_bytes());"
               "sys.exit(int(sys.argv[1]))")
    result = invoke(cwd, "run", "--log", log, "--", sys.executable, "-c", program, str(code))
    assert result.returncode == code, result
    assert (cwd / log).read_bytes() == payload
    return result.stdout


def verify_run(cwd):
    passed = b"test_invoice (__main__.Suite.test_invoice) ... ok\n"
    clean = capture(cwd, "ok.log", passed * 100 + b"Ran 100 tests\nOK\n")
    assert "test_invoice" not in clean and "=100" in clean and "Ran 100 tests" in clean
    for label, payload, code, signals in (
        ("early", b"FAILED tests/test_invoice.py::test_cents\n" + passed * 100 + b"finished\n", 1,
         ["1: FAILED", "finished"]),
        ("late", passed * 100 + b"FINAL: checksum mismatch in ledger\n", 17, ["101: FINAL", "exit=17"]),
        ("skip", passed + b"test_tax ... skipped 'engine missing'\nOK (skipped=1)\n", 0,
         ["engine missing", "skipped=1"]),
        ("none", b"Ran 0 tests in 0.001s\nOK\n", 0, ["Ran 0 tests"]),
        ("empty", b"", 0, ["REVIEW"]),
        ("warn", b"tests/test_x.py::test_x PASSED [100%]\nWarning: fallback parser\n", 0, ["fallback parser"]),
        ("rust", b"test ledger::balance ... ok\nerror: type mismatch\n", 101, ["type mismatch", "=1"]),
        ("build", b"[1/2] Building CXX object src/main.o\ncompiler: bad token\n"
         b"ninja: build stopped: subcommand failed.\n", 1, ["шагов сборки=1", "bad token", "build stopped"]),
        ("bytes", b"\xff bad byte\n", 2, ["\\xff"]),
        ("long", b"error: " + b"x" * 5000 + b" tail\n", 2, ["REVIEW"]),
        ("short", b"stage\n" * 35 + b"cause-tag=CASE732\n" + b"stage\n" * 35, 2, ["cause-tag=CASE732"]),
    ):
        summary = capture(cwd, label + ".log", payload, code)
        assert all(s in summary for s in signals), (label, summary)
    repeated = capture(cwd, "repeat.log", b"polling\n" * 600 + b"warning: retry\n" * 20
                       + b"FATAL: storage closed\n" + b"polling\n" * 40, 4)
    assert repeated.splitlines()[1:] == ["1-600: polling", "601-620: warning: retry",
                                        "621: FATAL: storage closed", "622-661: polling"], repeated
    common = b"x" * 500
    clipped = capture(cwd, "different.log", (common + b"A\n") * 10 + (common + b"B\n") * 10)
    assert "1-10:" in clipped and "11-20:" in clipped and "REVIEW" in clipped, clipped
    colored = capture(cwd, "colored.log", b"\x1b[31merror: unavailable\x1b[0m\n" * 15, 3)
    assert "1-15: error: unavailable" in colored and "\x1b" not in colored
    envelope = b'<Objs Version="1.1.0.1" xmlns="http://schemas.microsoft.com/powershell/2004/04">'
    progress = b'<Obj S="progress"><S N="Activity">loading modules</S></Obj>'
    xml = envelope + progress + b'</Objs>'
    displayed = capture(cwd, "powershell.log", b"#< CLIXML\r\n" + xml + b"\r\n" + xml + b"next step\r\n"
                        + b"before" + xml + b"after\r\n" + envelope + progress
                        + b'<S S="Error">missing &amp; failed</S><S S="Warning">retry</S></Objs>\r\n', 7)
    assert all(s in displayed for s in ("3: next step", "4: beforeafter", "5: Error: missing & failed", "Warning: retry"))
    assert "loading modules" not in displayed and "CLIXML" not in displayed
    full = capture(cwd, "short-powershell.log", xml + b"\n" + b"z" * 430 + b"\n")
    assert "Полный вывод" in full and "loading modules" not in full and "z" * 430 in full
    unknown = envelope + progress + b'<Obj S="error"><Ref RefId="0"/></Obj></Objs>'
    foreign = b'<Objs xmlns="urn:foreign"><Obj S="progress"/></Objs>'
    broken = envelope + b'<Obj S="progress"></Objs>'
    kept = capture(cwd, "unknown-xml.log", b"\n".join((unknown, foreign, broken)))
    assert all(x.decode() in kept for x in (unknown, foreign, broken))
    noise = b"".join(f"phase {i} ".encode() + b"x" * 100 + b"\n" for i in range(40))
    overflow = capture(cwd, "overflow.log", noise + b"FATAL: storage closed\n" + b"error!!\n" * 40, 4)
    assert "41: FATAL: storage closed" in overflow and "REVIEW" in overflow and "15" in overflow
    again = invoke(cwd, "run", "--log", "ok.log", "--", sys.executable, "-c", "raise SystemExit(9)")
    assert again.returncode != 0 and (cwd / "ok.log").read_bytes().endswith(b"Ran 100 tests\nOK\n")
    (cwd / "test_ledger.py").write_text(
        "import unittest\nclass Ledger(unittest.TestCase):\n"
        "    def test_amount(self): self.fail('wrong cents')\n"
        "    def test_load(self): raise ValueError('unknown currency')\n"
        "    @unittest.skip('no provider')\n    def test_remote(self): pass\n"
        "    def test_valid(self): self.assertTrue(True)\n", encoding="utf8")
    real = invoke(cwd, "run", "--log", "ledger.log", "--", sys.executable, "-m", "unittest", "-v", "test_ledger")
    assert real.returncode == 1 and all(label in real.stdout for label in
           ("wrong cents", "unknown currency", "no provider", "failures=1", "errors=1", "skipped=1")), real


def verify_audit(cwd):
    home = cwd / "home"
    home.mkdir()
    (home / "AGENTS.md").write_text("Global instructions loaded for each task.\n", encoding="utf8")
    child = cwd / "service"
    child.mkdir()
    duplicate = "Check the signed invoice against the current tax fixture."
    (cwd / "AGENTS.md").write_text(duplicate + "\nOther root guidance.\n", encoding="utf8")
    (child / "AGENTS.md").write_text("Shadowed\n", encoding="utf8")
    (child / "AGENTS.override.md").write_text(duplicate + "\n", encoding="utf8")
    deep = child / "deep"
    deep.mkdir()
    (deep / "TEAM.md").write_text("x" * 200 + "\n", encoding="utf8")
    (home / "config.toml").write_text(
        'project_doc_fallback_filenames=["TEAM.md"]\nproject_doc_max_bytes=150\n'
        '[mcp_servers.sample]\ncommand="fixture-private-command"\nenabled=false\n'
        '[mcp_servers.sample.env]\nTOKEN="fixture-private-value"\n', encoding="utf8")
    proc = invoke(deep, "audit", env=dict(os.environ, CODEX_HOME=str(home)))
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "$CODEX_HOME/AGENTS.md" in out and "AGENTS.md:1 = service/AGENTS.override.md:1" in out, out
    assert "service/AGENTS.md" not in out and "sample | off" in out and "fixture-private" not in out
    used = 150 - len((cwd / "AGENTS.md").read_bytes()) - len((child / "AGENTS.override.md").read_bytes())
    assert f"{used}/" in out and "service/deep/TEAM.md" in out and duplicate not in out, out


def verify_map(cwd):
    (cwd / "tracked.py").write_text("def discount_rate():\n    return 5\n", encoding="utf8")
    (cwd / "usage.ts").write_text("const rate = discount_rate();\n", encoding="utf8")
    (cwd / "ignored.py").write_text("def discount_rate(): pass", encoding="utf8")
    (cwd / ".gitignore").write_text("ignored.py\n", encoding="utf8")
    subprocess.run(["git", "add", "tracked.py"], cwd=cwd, check=True)
    (cwd / "too_large.py").write_bytes(b" " * (1024 ** 2 + 1))
    (cwd / "bad.py").write_bytes(b"\xff\n")
    res = invoke(cwd / "service", "map", "discount_rate", "--limit", "1")
    assert res.returncode == 0 and "tracked.py:1  discount_rate" in res.stdout, res
    assert "совпадениями: 2" in res.stdout and "Пропущено файлов:" in res.stdout
    all_rows = invoke(cwd, "map", "discount_rate")
    assert "usage.ts:1" in all_rows.stdout and "ignored.py" not in all_rows.stdout
    hidden = cwd / ".build"
    hidden.mkdir()
    (hidden / "rules").write_text("def release_gate(): pass\n", encoding="utf8")
    (hidden / "new").write_text("release_gate()\n", encoding="utf8")
    (cwd / "generated.py").write_text("release_gate()\n", encoding="utf8")
    with (cwd / ".gitignore").open("a") as ignored:
        ignored.write(".build/rules\ngenerated.py\n")
    subprocess.run(["git", "add", "-f", ".build/rules"], cwd=cwd, check=True)
    locations = invoke(cwd / "service", "map", "release_gate")
    output = locations.stdout.replace("\\", "/")
    assert locations.returncode == 0 and ".build/rules:1  release_gate" in output, locations
    assert output.splitlines()[0].endswith("Каталог: ..") and cwd.as_posix() not in output
    assert ".build/new:1" in output and "generated.py" not in output
    assert invoke(cwd, "map", "missing_widget").returncode == 1
    assert invoke(cwd, "map", "", "--limit", "0").returncode != 0


def verify_generic(cwd):
    (cwd / "условия").write_text("Продление по согласованию\n", encoding="utf8")
    (cwd / "support").mkdir()
    (cwd / "support" / "source.txt").write_text("Согласовано продление\n", encoding="utf8")
    (cwd / "import.zig").write_text("const renewal = true;\n", encoding="utf8")
    (cwd / "bytes.bin").write_bytes(b"\x00renewal")
    sources = invoke(cwd, "map", "продление").stdout.replace("\\", "/")
    assert "условия:1" in sources and "support/source.txt:1" in sources, sources
    found = invoke(cwd, "map", "renewal")
    assert "import.zig:1" in found.stdout and "bytes.bin" not in found.stdout
    args = ("reuse", "--input", "условия", "--input", "layout.txt", "--result", "export.bin",
            "--record", "export.receipt", "--method", "Export 1.0 A4")
    (cwd / "layout.txt").write_text("A4")
    artifact = cwd / "export.bin"
    artifact.write_bytes(b"\x01\x02")
    assert invoke(cwd, *args).returncode != 0
    assert invoke(cwd, *args, "--save").returncode == 0
    assert invoke(cwd, *args).stdout.strip() == "UNCHANGED"
    assert invoke(cwd, *args[:-1], "Export 1.0 A3").returncode != 0
    (cwd / "layout.txt").write_text("A3")
    assert invoke(cwd, *args).returncode != 0
    (cwd / "layout.txt").write_text("A4")
    artifact.write_bytes(b"\x01\x03")
    assert invoke(cwd, *args).returncode != 0
    assert invoke(cwd, *args, "--save").returncode == 0
    assert invoke(cwd, *args).returncode == 0
    receipt = cwd / "export.receipt"
    receipt.write_text("user data")
    assert invoke(cwd, *args, "--save").returncode != 0 and receipt.read_text() == "user data"


if __name__ == "__main__":
    from project_context_cases import run_checks
    with temporary_profile() as folder:
        run_checks(folder, TOOL)
    with temporary_profile() as plain_folder:
        verify_generic(plain_folder)
    with temporary_profile() as cwd:
        subprocess.run(["git", "init", "-q"], cwd=cwd, check=True)
        verify_run(cwd)
        verify_audit(cwd)
        verify_map(cwd)
    print("PASS: summaries and raw evidence, instruction layers and MCP, ranked source locations")
