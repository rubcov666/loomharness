"""Local, on-demand tools for inspecting command output and project context."""

import argparse
from collections import deque
import hashlib
from itertools import groupby
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import stat
import tempfile
from threading import Timer


ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
# Suppress only individual success records. Keep summaries, skips and diagnostics.
SUCCESS = re.compile(
    r"(?:test[\w.]* \([\w.]+\) \.\.\. ok"
    r"|[\w./\\:-]+::[\w.:-]+ PASSED(?:\s+\[\s*\d+%\])?"
    r"|test [\w:]+ \.\.\. ok)$"
)
BUILD_STEP = re.compile(r"\[\s*\d+/\d+\]\s+(?:Building (?:C|CXX) object|Linking (?:C|CXX) executable) [\w./\\:+-]+$")
DIAGNOSTIC = re.compile(
    r"\b(?:fail(?:ed|ure|ures)?|error(?:s)?|fatal|panic|skip(?:ped)?|xfail|xpass|"
    r"warning(?:s)?|traceback|cancelled|timed out|no tests|ran 0 tests)\b|\w+(?:Error|Exception):", re.I
)
CLIXML_DOC = re.compile(r"<Objs(?=[\s>])[^\r\n]*?</Objs>")
PS_NS = "{http://schemas.microsoft.com/powershell/2004/04}"


def compact_powershell(line):
    """Remove serialized progress from the display copy of a merged SSH log."""
    if line.rstrip("\r\n") == "#< CLIXML":
        return ""
    if "<Objs" not in line or "</Objs>" not in line or len(line) > 262144:
        return line

    from xml.etree import ElementTree

    def replace(match):
        raw = match[0]
        if "progress" not in raw or "<!" in raw or "<?" in raw:
            return raw
        try:
            root = ElementTree.fromstring(raw)
        except ElementTree.ParseError:
            return raw
        if root.tag != PS_NS + "Objs" or (root.text or "").strip():
            return raw
        messages = []
        removed = False
        for record in root:
            stream = record.get("S", "").casefold()
            if (record.tail or "").strip():
                return raw
            if record.tag == PS_NS + "Obj" and stream == "progress":
                removed = True
            elif (record.tag == PS_NS + "S" and not len(record) and
                  stream in {"error", "warning", "information", "verbose", "debug", "output"}):
                messages.append(f"{record.get('S')}: {record.text or ''}")
            else:
                return raw  # Unknown types or object references need their original envelope.
        return " | ".join(messages) if removed else raw

    visible = CLIXML_DOC.sub(replace, line)
    return visible if visible.strip() else ""


def capture(args):
    command = args.argv
    if command[:1] != ["--"] or len(command) < 2:
        raise ValueError("Укажите программу и аргументы после --.")
    path = Path(args.log)
    # Never overwrite evidence from another run. Keep the child's original bytes.
    with path.open("xb") as logfile:
        print(f"Полный лог: {path}", file=sys.stderr, flush=True)
        result = subprocess.run(command[1:], stdout=logfile, stderr=subprocess.STDOUT)
    status = result.returncode if result.returncode >= 0 else 128 - result.returncode
    passed = steps = total = relevant = 0
    detail, priority = [], []
    tail = deque(maxlen=3)
    clipped = False
    first_omitted = None
    # Two bounded lists preserve early unfamiliar text and late diagnostics.
    with path.open("r", encoding="utf-8-sig", errors="backslashreplace") as logfile:
        for raw, copies in groupby(logfile):
            first = total + 1
            for _ in copies:
                total += 1
            count = total - first + 1
            visible = compact_powershell(raw)
            line = (ANSI.sub("", visible) if "\x1b" in visible else visible).strip()
            if not line:
                continue
            if SUCCESS.fullmatch(line):
                passed += count
                continue
            if BUILD_STEP.fullmatch(line):
                steps += count
                continue
            relevant += count
            item = (first, total, line[:420])
            tail.append(item)
            if len(line) > 420:
                clipped = True
                if first_omitted is None:
                    first_omitted = first
            if len(detail) < 14:
                detail.append(item)
            elif DIAGNOSTIC.search(line) and len(priority) < 14:
                priority.append(item)
            elif first_omitted is None:
                first_omitted = first
    counts = f" | отдельных успешных тестов={passed}" if passed else ""
    if steps:
        counts += f" | шагов сборки={steps}"
    print(f"exit={result.returncode} | строк={total}{counts}")
    shown = sorted(set(detail + priority + list(tail)))
    represented = sum(last - first + 1 for first, last, _ in shown)
    incomplete = represented < relevant or clipped
    if incomplete and path.stat().st_size <= 4096:
        # A second model turn can cost more than a complete short log.
        print("Полный вывод:")
        with path.open(encoding="utf-8-sig", errors="backslashreplace") as logfile:
            print(ANSI.sub("", "".join(compact_powershell(line) for line in logfile)).rstrip("\n"))
        return status
    for first, last, line in shown:
        location = str(first) if first == last else f"{first}-{last}"
        print(f"{location}: {line}")
    if incomplete:
        print(f"REVIEW: сокращено строк по количеству или длине. "
              f"Показано {represented} из {relevant} строк подробностей. "
              f"Откройте полный лог со строки {first_omitted}.")
    if total == 0:
        print("REVIEW: пустой журнал. Проверьте результат по самому объекту.")
    elif not (relevant or passed or steps):
        print("REVIEW: журнал не содержит содержательного вывода.")
    return status


def codex_request(cwd, method, params, *, executable=None):
    """Query the local Codex app-server for this working directory."""
    exe = shutil.which(executable or "codex")
    if not exe:
        raise ValueError("Для аудита установите Codex CLI и добавьте его в PATH.")
    with subprocess.Popen([exe, "app-server", "--listen", "stdio://"], cwd=cwd,
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          text=True, encoding="utf8") as proc:
        def kill_if_running():
            # PID может быть переиспользован, если codex уже завершился.
            if proc.poll() is None:
                proc.kill()
        timer = Timer(15, kill_if_running)
        timer.start()
        try:
            def request(number, method, params):
                message = {"method": method, "params": params}
                if number is not None:
                    message["id"] = number
                proc.stdin.write(json.dumps(message) + "\n")
                proc.stdin.flush()
                if number is None:
                    return
                for raw in proc.stdout:
                    reply = json.loads(raw)
                    if reply.get("id") == number:
                        if "error" in reply:
                            error = reply["error"]
                            raise ValueError(f"Codex {method}: {error.get('message', 'request failed')} ({error.get('code')})")
                        return reply["result"]
                raise ValueError(f"Codex не вернул результат {method}.")

            request(1, "initialize", {"clientInfo": {"name": "harness_audit", "version": "1"},
                                      "capabilities": {"experimentalApi": True}})
            request(None, "initialized", {})
            return request(2, method, params)
        finally:
            try:
                proc.stdin.close()
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            finally:
                timer.cancel()


def codex_config(cwd):
    """Ask Codex to resolve the same config layers it uses for this directory."""
    return codex_request(cwd, "config/read", {"cwd": str(cwd), "includeLayers": False})["config"]


def visible_instructions(cwd):
    # This local preview does not start a model turn. Keep all prompt contents
    # in memory and return only the user-role AGENTS block for matching.
    try:
        result = subprocess.run([shutil.which("codex"), "--disable", "hooks", "debug", "prompt-input"],
                                cwd=cwd, capture_output=True, timeout=15)
        if result.returncode:
            return None
        entries = json.loads(result.stdout)
        if not isinstance(entries, list):
            return None
        blocks = []
        for item in entries:
            if item.get("role") != "user":
                continue
            for chunk in item.get("content", []):
                text = chunk.get("text", "").replace("\r\n", "\n")
                if text.startswith("# AGENTS.md instructions for ") and "\n<INSTRUCTIONS>\n" in text:
                    blocks.append(text.split("\n<INSTRUCTIONS>\n", 1)[1].rsplit("\n</INSTRUCTIONS>", 1)[0])
        return "\n".join(blocks)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None


def audit(cwd, config):
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").resolve()
    markers = config.get("project_root_markers")
    if markers is None:
        markers = [".git"]
    root = next((p for p in (cwd, *cwd.parents) if any((p / m).exists() for m in markers)), cwd)
    dirs = [root]
    for part in cwd.relative_to(root).parts:
        dirs.append(dirs[-1] / part)
    names = ["AGENTS.override.md", "AGENTS.md"]
    fallback = config.get("project_doc_fallback_filenames") or []
    remaining = config.get("project_doc_max_bytes", 32768)
    visible = visible_instructions(cwd)
    seen = {}
    duplicates = []
    print("Файлы AGENTS для текущей папки (сверху вниз):")
    if visible is None:
        print("Предпросмотр CLI недоступен. Ниже оценка по конфигурации и файлам на диске.")
    for directory, global_scope in [(home, True), *((p, False) for p in dirs)]:
        for name in (names if global_scope else names + fallback):
            if Path(name).name != name or name in {"", ".", ".."}:
                continue
            path = directory / name
            if not path.is_file():
                continue
            raw = path.read_bytes()
            if not raw.strip():
                if global_scope:
                    continue
                break
            label = ("$CODEX_HOME/" + name if global_scope else path.relative_to(root).as_posix())
            used = len(raw) if global_scope else min(remaining, len(raw))
            decoded = raw[:used].decode("utf-8-sig", errors="replace").replace("\r\n", "\n")
            needle = decoded.strip()
            if visible is not None and needle:
                at = visible.find(needle)
                if at < 0:
                    print(f"  {label} | не подтверждён в предпросмотре CLI")
                    break
                visible = visible[at + len(needle):]
            if not global_scope and needle:
                remaining -= used
            print(f"  {label} | {used}/{len(raw)} байт")
            # A clipped last line is not evidence of a duplicate instruction.
            content = decoded.splitlines()
            if used < len(raw) and not raw[:used].endswith((b"\n", b"\r")):
                content = content[:-1]
            for number, line in enumerate(content, 1):
                key = line.strip()
                if len(key) < 24:
                    continue
                location = f"{label}:{number}"
                if key in seen:
                    duplicates.append((seen[key], location))
                else:
                    seen[key] = location
            break
    print(f"Повторяющиеся строки от 24 знаков: {len(duplicates)}")
    for a, b in duplicates[:12]:
        print(f"  {a} = {b}")
    if len(duplicates) > 12:
        print("Показаны первые 12 пар.")
    servers = config.get("mcp_servers") or {}
    print("Настроенные MCP (подключение к серверам не проверяется):")
    for name, server in sorted(servers.items()):
        print(f"  {name} | {'on' if server.get('enabled', True) else 'off'}")
    if not servers:
        print("  нет в конфигурации CLI")


DECLARATION = re.compile(
    r"\b(?:def|class|function|fn|interface|struct|enum|type|trait|func|const|let|sub|module|"
    r"fun|procedure)\s+(?:\([^()]*\)\s*)?([\w$]+)", re.I
)


def file_list(cwd):
    git = shutil.which("git")
    if git and (os.environ.get("GIT_DIR") or any((d / ".git").exists() for d in (cwd, *cwd.parents))):
        root = Path(os.fsdecode(subprocess.check_output([git, "rev-parse", "--show-toplevel"], cwd=cwd)).rstrip("\r\n"))
        raw = subprocess.check_output([git, "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=root)
        return root.resolve(), {os.fsdecode(entry) for entry in raw.split(b"\0") if entry}, git
    if rg := shutil.which("rg"):
        listed = subprocess.run([rg, "--files", "--null", "--no-require-git"], cwd=cwd, capture_output=True)
        if listed.returncode not in (0, 1):
            raise ValueError("Не удалось перечислить файлы рабочей папки.")
        return cwd, {Path(os.fsdecode(entry)).as_posix() for entry in listed.stdout.split(b"\0") if entry}, None
    paths = []
    for directory, dirs, files in os.walk(cwd):
        base = Path(directory)
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in {"node_modules", "__pycache__"}
                   and not (base / d).is_symlink()
                   and not getattr((base / d).lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT]
        paths.extend((base / f).relative_to(cwd).as_posix() for f in files if not f.startswith("."))
    return cwd, paths, None


def content_misses(cwd, term, git):
    # Exclude only files actually searched. Git can track files that rg skips.
    if rg := shutil.which("rg"):
        command = [rg, "--no-config", "--files-without-match", "--null", "--no-require-git", "--max-filesize", "1M",
                   "--fixed-strings", "--ignore-case", "--", term, "."]
    elif git and term.isascii():
        command = [git, "grep", "--untracked", "--exclude-standard", "--no-textconv",
                   "-L", "-z", "-i", "-F", "-e", term, "--"]
    else:
        return set()
    listed = subprocess.run(command, cwd=cwd, capture_output=True)
    if listed.returncode not in (0, 1):
        return set()
    return {Path(os.fsdecode(entry)).as_posix() for entry in listed.stdout.split(b"\0") if entry}


def code_map(cwd, query, limit):
    terms = query.casefold().split()
    if not terms or not 1 <= limit <= 50:
        raise ValueError("Нужен поисковый запрос и --limit от 1 до 50.")
    root, paths, git = file_list(cwd.resolve())
    selective = max(terms, key=len)
    misses = content_misses(root, selective, git)
    results = []
    skipped = 0
    for name in paths:
        folded_name = name.casefold()
        if name in misses and selective not in folded_name:
            continue
        content_terms = [term for term in terms if term not in folded_name]
        path = root / name
        best = None
        try:
            if not path.is_file():
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(root) or path.stat().st_size > 1024 * 1024:
                skipped += 1
                continue
            with path.open(encoding="utf-8-sig") as handle:
                for number, line in enumerate(handle, 1):
                    if "\0" in line:
                        raise ValueError("binary")
                    folded_line = line.casefold()
                    if not all(term in folded_line for term in content_terms):
                        continue
                    match = DECLARATION.search(line)
                    symbol = match.group(1) if match else ""
                    score = (sum(t in symbol.casefold() for t in terms),
                             sum(t in folded_line for t in terms), bool(symbol))
                    if best is None or score > best[0]:
                        best = (score, number, symbol)
        except (OSError, ValueError, RuntimeError):
            skipped += 1
            continue
        if best:
            score, number, symbol = best
            results.append((score, name, number, symbol))
    results.sort(key=lambda row: (tuple(-int(x) for x in row[0]), row[1]))
    print(f"Файлов с совпадениями: {len(results)}. Показано: {min(limit, len(results))}. "
          f"Пропущено файлов: {skipped}. Каталог: {Path(os.path.relpath(root, cwd.resolve())).as_posix()}")
    for _, name, number, symbol in results[:limit]:
        print(f"{name}:{number}" + (f"  {symbol}" if symbol else ""))
    return 0 if results else 1


def reuse_result(args):
    """Compare explicitly declared local inputs, outputs and method. Never execute the action."""
    if not args.method.strip():
        raise ValueError("Укажите способ получения результата, версию и значимые настройки.")
    record = args.record.absolute()
    requested = (*args.input, *args.result)
    files = [p.resolve() for p in requested]
    if record.resolve() in files or set(files[:len(args.input)]) & set(files[len(args.input):]):
        raise ValueError("Разделите исходные файлы, готовый результат и запись проверки.")
    # A record is just a digest. Never read a path or execute a command from it.
    marker = "harness-kit/local-result-v1"  # сохраняем прежнее значение: это метка persisted-рецептов context.py reuse
    try:
        with record.open("rb") as stream:
            previous = stream.read(257)
        if len(previous) > 256:
            raise ValueError("Путь записи занят другим файлом.")
        stored = json.loads(previous)
        if not isinstance(stored, dict) or stored.get("format") != marker or not re.fullmatch(
                r"[0-9a-f]{64}", str(stored.get("sha256", ""))):
            raise ValueError("Путь записи занят другим файлом.")
    except FileNotFoundError:
        if not args.save:
            print("RECHECK: запись для сравнения отсутствует.")
            return 1
        previous = stored = None
    def stamp(info):
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
    entries, stamps = [], []
    for p in files:
        if not p.is_file():
            if args.save:
                raise ValueError(f"Для записи нужен обычный файл: {p}")
            print(f"RECHECK: файл недоступен: {p}")
            return 1
        path_info = p.stat()
        with p.open("rb") as stream:
            handle_info = os.fstat(stream.fileno())
            if not os.path.samestat(path_info, handle_info):
                raise ValueError(f"Файл был заменён: {p}")
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if stamp(handle_info) != stamp(os.fstat(stream.fileno())):
                raise ValueError(f"Файл изменился при чтении: {p}")
        entries.append((os.path.normcase(str(p)), digest))
        stamps.append(stamp(path_info))
    if any(stamp(p.stat()) != known for p, known in zip(requested, stamps)):
        raise ValueError("Файлы изменились во время сверки.")
    names = [os.path.normcase(str(p.absolute())) for p in requested]
    payload = json.dumps([args.method, len(args.input), names, entries], ensure_ascii=True).encode()
    expected = {"format": marker, "sha256": hashlib.sha256(payload).hexdigest()}
    if not args.save:
        print("UNCHANGED" if expected == stored else "RECHECK: входы, результат или способ изменились.")
        return int(expected != stored)
    if expected != stored:
        with tempfile.NamedTemporaryFile(dir=record.parent, delete=False) as stream:
            tmp = Path(stream.name)
            stream.write(json.dumps(expected).encode())
        try:
            if stored is None:
                if os.name == "nt":
                    tmp.rename(record)
                else:
                    os.link(tmp, record)
            else:
                if record.read_bytes() != previous:
                    raise ValueError("Запись параллельно изменилась. Повторите сверку.")
                tmp.replace(record)
        finally:
            tmp.unlink(missing_ok=True)
    print(f"RECORDED: входов {len(args.input)}, результатов {len(args.result)}.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    run = sub.add_parser("run", help="Выполнить команду с сохранением полного лога.")
    run.add_argument("--log", required=True, help="Новый файл в существующей папке.")
    run.add_argument("argv", nargs=argparse.REMAINDER)
    sub.add_parser("audit", help="Проверить инструкции и настроенные MCP для текущей папки.")
    reuse = sub.add_parser("reuse", help="Сверить входы и готовые локальные результаты любого формата.")
    reuse.add_argument("--record", required=True, type=Path)
    reuse.add_argument("--method", required=True, help="Способ получения, версия и значимые настройки.")
    reuse.add_argument("--input", required=True, type=Path, action="append")
    reuse.add_argument("--result", required=True, type=Path, action="append")
    reuse.add_argument("--save", action="store_true", help="Записать версии после проверки самого результата.")
    search = sub.add_parser("map", help="Найти связанные текстовые файлы и строки в рабочей папке.")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=8)
    symbol = sub.add_parser("symbol", help="read one Java method and its overloads from a local file")
    symbol.add_argument("path", type=Path)
    symbol.add_argument("name", help="method or declaring type and method, e.g. Catalog.lookup")
    build = sub.add_parser("build-check", help="summarize tests for an exact build, or run a local build/test recipe")
    build.add_argument("index", type=Path, nargs="?")
    build.add_argument("--recipe", type=Path)
    identity = build.add_mutually_exclusive_group()
    identity.add_argument("--revision", metavar="SHA256")
    identity.add_argument("--artifact", type=Path)
    identity.add_argument("--build-report", type=Path)
    build.add_argument("--require", action="append", default=[], metavar="RUN", help="require this run label")
    args = parser.parse_args()
    cwd = Path.cwd().resolve()
    if args.action in ("symbol", "build-check"):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import hk
        if args.action == "symbol":
            return hk.load("symbols")["show"](args.path, args.name)
        return hk.load("build_check")["check"](args)
    if args.action == "run":
        return capture(args)
    if args.action == "reuse":
        return reuse_result(args)
    if args.action == "audit":
        audit(cwd, codex_config(cwd))
        return 0
    return code_map(cwd, args.query, args.limit)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
