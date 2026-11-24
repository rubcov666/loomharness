"""Compile and run the consumer, including a regression beyond its initial check."""
import concurrent.futures
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import zipfile


SCRIPT = Path(os.environ.get("LOOMHARNESS_REPO", Path(__file__).resolve().parents[1])) / "loomharness/scripts/javac_cache.py"
settings = os.fsdecode(subprocess.check_output(["java", "-XshowSettings:properties", "-version"],
                                             stderr=subprocess.STDOUT))
jdk = Path(re.search(r"(?m)^\s*java.home = (.+)$", settings).group(1).strip())
release = re.search(r"(?m)^\s*java.specification.version = (\d+)", settings).group(1)
with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    src = root / "Flow.java"
    jar = root / "support.jar"
    def make_jar(contents, manifest="Manifest-Version: 1.0\r\n\r\n"):
        with zipfile.ZipFile(jar, "w") as archive:
            archive.writestr("META-INF/MANIFEST.MF", manifest)
            archive.writestr("stamp", contents)
    make_jar("v1")
    cmd = [sys.executable, "-I", "-X", "utf8", SCRIPT, "--jdk", jdk, "--release", release,
           "--cache-dir", root / "cache", "--jar", jar, src]
    def build():
        p = subprocess.run(cmd, cwd=root, capture_output=True)
        assert p.returncode == 0, p.stderr
        return Path(p.stdout.decode("utf8").strip()), p.stderr
    def flow(classes, step):
        return subprocess.run([jdk / "bin/java", "-cp", classes, "Flow", step],
                              cwd=root, capture_output=True).returncode
    old = ('public class Flow { public static void main(String[] a) {'
           'boolean first = false; boolean arrived = false;'
           'if (!first || (a[0].equals("full") && !arrived)) System.exit(3); } }')
    src.write_text(old, encoding="utf8")
    first, log = build()
    assert b"compiled" in log and flow(first, "first") == 3
    unchanged, log = build()
    assert b"cache hit" in log and unchanged == first
    # A shell wrapper reads the path independently of the console's encoding.
    pointer = root / "compiled classes Ω.txt"
    wrapper = [*cmd, "--path-file", pointer]
    for expected in (b"compiled", b"cache hit"):
        p = subprocess.run(wrapper, capture_output=True, check=True)
        assert expected in p.stdout
        assert flow(Path(pointer.read_text(encoding="utf8")), "first") == 3
        pointer.unlink()
    src.write_text(old.replace("first = false", "first = true"), encoding="utf8")
    partial, _ = build()
    assert flow(partial, "first") == 0 and flow(partial, "full") == 3
    src.write_text(src.read_text(encoding="utf8").replace("arrived = false", "arrived = true"), encoding="utf8")
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        concurrent = list(pool.map(lambda _: build(), range(2)))
    assert all(flow(folder, "full") == 0 for folder, _ in concurrent)
    good, _ = build()
    assert good != partial and flow(good, "first") == flow(good, "full") == 0
    (good / "Flow.class").write_bytes(b"broken")
    fixed, log = build()
    assert fixed != good and b"compiled" in log and flow(fixed, "full") == 0
    (fixed / "Stale.class").write_bytes(b"stale")
    clean, _ = build()
    assert not (clean / "Stale.class").exists()
    make_jar("v2")
    changed, _ = build()
    assert clean != changed
    ref = changed.parent.parent / "current.json"
    ref.write_text("{corrupt")
    assert build()[0] != changed
    make_jar("v3", "Manifest-Version: 1.0\r\nClass-Path: unknown.jar\r\n\r\n")
    p = subprocess.run(cmd, capture_output=True)
    assert p.returncode and b"Indirect JAR" in p.stderr and not p.stdout
    for extra in ("META-INF/services/javax.annotation.processing.Processor", "generated/Types.java"):
        make_jar("v4")
        with zipfile.ZipFile(jar, "a") as archive:
            archive.writestr(extra, "example.Type")
        p = subprocess.run(wrapper, capture_output=True)
        assert p.returncode not in (0, 65) and not pointer.exists()
    make_jar("v4")
    src.write_text("public class Flow { ??? }", encoding="utf8")
    p = subprocess.run(cmd, capture_output=True)
    assert p.returncode and b"Flow.java" in p.stderr and not p.stdout
    p = subprocess.run(wrapper, capture_output=True)
    assert p.returncode == 65 and b"Flow.java" in p.stderr and not pointer.exists()
    src.write_text("import java.util.*; public class Flow { List<String> x = new ArrayList(); }", encoding="utf8")
    for _ in range(2):
        assert b"diagnostics require a fresh run" in build()[1]
print("PASS: cold and warm builds, changed inputs, corruption, concurrent builds, javac errors and final outcome")
