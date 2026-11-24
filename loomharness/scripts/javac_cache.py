"""Compile an explicit set of ordinary Java sources and print the classes directory."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def stamp(path):
    # Size+mtime is exact for cache reuse; a change that preserves both stamps is
    # not a realistic compiler update scenario, and it avoids re-hashing 100+ MB
    # of lib/modules or every cached class on each call.
    info = path.stat()
    return f"{info.st_size}:{info.st_mtime_ns}"


def classes(folder):
    if not folder.is_dir() or folder.resolve() != folder:
        return {}
    result = {}
    for path in folder.rglob("*"):
        # lstat-based check instead of resolve() per entry: on Windows resolve()
        # costs a system call per path and dominated hit checks (~3.5x). Symlinked
        # entries are refused; other reparse points (OneDrive placeholders) pass,
        # matching the previous resolve() semantics.
        if path.is_symlink():
            return {}
        if path.is_dir():
            continue
        if path.suffix != ".class" or not path.is_file():
            return {}
        result[path.relative_to(folder).as_posix()] = stamp(path)
    return result


def hit(entry):
    try:
        ref = json.loads((entry / "current.json").read_bytes())
        if not re.fullmatch("[0-9a-f]{32}", ref["generation"]):
            return None
        folder = entry / ref["generation"] / "classes"
        if ref["files"] and classes(folder) == ref["files"]:
            return folder
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        pass
    return None


def inputs(paths, suffix):
    result = []
    for path in paths:
        if path.suffix != suffix or not path.is_file() or path.name == "module-info.java":
            raise ValueError(f"Requires explicit ordinary {suffix} files: {path}")
        # Исходники — полный дайджест; толстые JAR — дешёвый stamp (size+mtime),
        # тот же приём, что и для lib/modules toolchain.
        full = suffix == ".java"
        result.append((path.name, digest(path) if full else stamp(path), full))
    return result


def stage(paths, identities, target):
    staged = []
    for number, (path, (name, expected, full)) in enumerate(zip(paths, identities)):
        before = path.stat()
        dest = target / str(number) / name
        dest.parent.mkdir(parents=True)
        shutil.copyfile(path, dest)
        if before.st_size != path.stat().st_size or dest.stat().st_size != before.st_size:
            raise ValueError(f"Input changed while preparing compilation: {path}")
        if full and digest(dest) != expected:
            raise ValueError(f"Input changed while preparing compilation: {path}")
        staged.append(dest)
    return staged


def check_jar(path, *, preserve_compiler_defaults):
    with zipfile.ZipFile(path) as archive:
        names = {n.upper(): n for n in archive.namelist()}
        manifest = names.get("META-INF/MANIFEST.MF")
        if ("META-INF/INDEX.LIST" in names or manifest and
                re.search(br"(?im)^class-path\s*:", archive.read(manifest))):
            raise ValueError(f"Indirect JAR dependencies require the project build: {path.name}")
        if preserve_compiler_defaults and any(
            n.endswith(".JAVA") or n.endswith("META-INF/SERVICES/JAVAX.ANNOTATION.PROCESSING.PROCESSOR")
            for n in names
        ):
            raise ValueError(f"Source discovery or annotation processing requires the project build: {path.name}")


def publish_path(folder, path_file, message):
    if path_file is None:
        print(message, file=sys.stderr)
        print(folder)
    else:
        with path_file.open("x", encoding="utf8") as output:
            output.write(str(folder))
        print(message)


def compile_java(args):
    for var in ("JDK_JAVAC_OPTIONS", "JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "JDK_JAVA_OPTIONS"):
        if os.environ.get(var):
            raise ValueError(f"{var} is set. Use the project's own compiler invocation.")
    if not args.jdk:
        raise ValueError("Specify --jdk or set JAVA_HOME to a JDK installation.")
    jdk = Path(args.jdk).resolve()
    javac = jdk / "bin" / ("javac.exe" if os.name == "nt" else "javac")
    # Small launcher and release file get a full digest; the large runtime image
    # files get a cheap size+mtime stamp instead of a 200 MB re-hash per call.
    toolchain = [digest(javac), digest(jdk / "release"),
                 stamp(jdk / "lib/modules"), stamp(jdk / "lib/ct.sym")]
    src_ids = inputs(args.sources, ".java")
    jar_ids = inputs(args.jar, ".jar")
    # A digest of this script also invalidates old entries after recipe changes.
    identity = [digest(Path(__file__)), str(jdk), toolchain,
                args.release, src_ids, jar_ids, args.path_file is not None]
    key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    entry = args.cache_dir.resolve() / key
    folder = hit(entry)
    if folder:
        publish_path(folder, args.path_file, "cache hit")
        return 0
    entry.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=entry) as tmp:
        work = Path(tmp)
        sources = stage(args.sources, src_ids, work / "sources")
        jars = stage(args.jar, jar_ids, work / "jars")
        for jar in jars:
            check_jar(jar, preserve_compiler_defaults=args.path_file is not None)
        empty = work / "empty"
        empty.mkdir()
        output = work / "build/classes"
        output.mkdir(parents=True)
        cp = os.pathsep.join(map(str, jars)) or str(empty)
        if any(os.pathsep in str(p) for p in [*jars, empty]):
            raise ValueError("Cache path contains a classpath separator.")
        cmd = [javac, "--release", str(args.release), "-encoding", "UTF-8", "-proc:none",
               "-implicit:none", "-sourcepath", empty, "-classpath", cp, "-d", output, *sources]
        with (work / "diagnostics").open("w+b") as log:
            proc = subprocess.run(cmd, cwd=work, stdout=log, stderr=subprocess.STDOUT)
            has_diagnostics = log.tell() > 0
            if has_diagnostics:
                log.seek(0)
                shutil.copyfileobj(log, sys.stderr.buffer)
        if proc.returncode:
            return 65 if args.path_file is not None else proc.returncode
        files = classes(output)
        if not files:
            raise ValueError("Compiler produced no reusable classes.")
        gen = uuid.uuid4().hex
        (work / "build").rename(entry / gen)
        if not has_diagnostics:
            ref = work / "current.json"
            ref.write_text(json.dumps({"generation": gen, "files": files}), encoding="utf8")
            ref.replace(entry / "current.json")
            # The cache used to grow without bound: generations no longer
            # referenced by current.json are dead weight. current.json is already
            # replaced, so a racing hit() either sees the new generation or gets
            # an OSError and falls back to a fresh compile. Only stale
            # generations are removed: a concurrent build may still be handing
            # out a folder it just published.
            now = time.time()
            for child in entry.iterdir():
                if (child.name != gen and child.is_dir() and re.fullmatch(r"[0-9a-f]{32}", child.name)
                        and now - child.stat().st_mtime > 24 * 3600):
                    shutil.rmtree(child, ignore_errors=True)
        message = "compiled; diagnostics require a fresh run" if has_diagnostics else "compiled"
        publish_path(entry / gen / "classes", args.path_file, message)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jdk", default=os.environ.get("JAVA_HOME"))
    parser.add_argument("--release", required=True, type=int)
    parser.add_argument("--cache-dir", required=True, type=Path)
    parser.add_argument("--path-file", type=Path,
                        help="For project wrappers: write the UTF-8 classes path to a new file. "
                             "Exit 65 means javac failed; other failures permit the native build.")
    parser.add_argument("--jar", action="append", default=[], type=Path)
    parser.add_argument("sources", nargs="+", type=Path)
    args = parser.parse_args()
    try:
        return compile_java(args)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
