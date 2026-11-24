"""Inspect an existing file by content, excerpt, or exact byte identity."""

import argparse
from collections import deque
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

JSON_PART = re.compile(r'"(?:[^"\\]|\\.)*"|[ \r\t\n]+')
IDENT = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\Z")
SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z", re.ASCII)
STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)\Z", re.ASCII)
WORDS = re.compile(r"\w+|[^\w\s]")
# Максимум одного вывода в чат. 48 КБ — это ~12k токенов: безопасный потолок
# для одного ответа инструмента. Точный фрагмент добывается через --find.
RENDER_CAP = 48 * 1024
# Входной файл больше этого не читаем целиком в память: вывод всё равно
# упрётся в RENDER_CAP, а точный фрагмент добывается через --find.
MAX_INPUT_BYTES = 256 * 1024 * 1024


def weight(text):
    # Offline size proxy. Count punctuation/words as well as UTF-8 bytes:
    # a long hash and a line of JSON punctuation tokenize very differently.
    return len(text.encode("utf8")) + 3 * sum(1 for _ in WORDS.finditer(text))


def choose(full, candidates):
    floor = weight(full)
    best, cost = full, floor
    for candidate in candidates:
        size = weight(candidate)
        if size < cost:
            best, cost = candidate, size
    return best if floor - cost >= max(96, floor // 8) else full


def unique_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Повторяющийся ключ JSON. Проверьте исходный файл.")
    return result


class NumberLiteral(str):
    """Preserve JSON number spellings that cannot safely go through a table codec."""


def integer(text):
    if text == "-0" or len(text) > 16 or abs(int(text)) > 2**53 - 1:
        return NumberLiteral(text)
    return int(text)


def invalid_constant(value):
    raise ValueError(f"Недопустимое значение JSON: {value}")


def portable(value):
    if isinstance(value, dict):
        return all(portable(k) and portable(v) for k, v in value.items())
    if isinstance(value, list):
        return all(portable(v) for v in value)
    return not isinstance(value, NumberLiteral) and (
        not isinstance(value, str) or not re.search(r"[\ud800-\udfff]", value)
    )


def incidental(key, value):
    # Only omit recognized transport metadata with the expected shape. In
    # particular, keep free text and unfamiliar/nested diagnostic fields.
    if not isinstance(value, str):
        return False
    if key in {"headSha", "oid"}:
        return bool(SHA.fullmatch(value))
    if key in {"createdAt", "updatedAt"}:
        if not STAMP.fullmatch(value):
            return False
        try:
            datetime.fromisoformat(value)
        except ValueError:
            return False
        return True
    if key in {"url", "html_url"}:
        link = re.fullmatch(
            r"https?://[A-Za-z0-9.-]+(?::\d+)?/(?:[\w.~-]+/)*(?:\d+|[0-9a-f]{40})", value
        )
        return bool(link) and not re.search(r"error|fail|warn|panic|exception|diagnostic", value, re.I)
    return False


def project(rows, required):
    if not required or not all(required.issubset(row) for row in rows):
        return None
    metadata = {"headSha", "oid", "createdAt", "updatedAt", "url", "html_url"}
    omit = sorted(
        k for k in metadata - required if all(k in row and incidental(k, row[k]) for row in rows)
    )
    if not omit:
        return None
    keep = [{k: v for k, v in row.items() if k not in omit} for row in rows]
    note = f"All {len(rows)} rows, original order. Omitted metadata: {', '.join(omit)}.\n"
    return note, keep


def table(rows):
    keys = list(rows[0])
    if not keys or any(
        not IDENT.fullmatch(k) or k in {"__proto__", "constructor", "prototype"} for k in keys
    ):
        return None
    if any(row.keys() != rows[0].keys() for row in rows):
        return None
    lines = [f"[{len(rows)}]{{{','.join(keys)}}}:"]
    for row in rows:
        cells = []
        for k in keys:
            value = row[k]
            if isinstance(value, (dict, list)):
                return None
            if isinstance(value, str):
                if IDENT.fullmatch(value) and value not in {"false", "true", "null"}:
                    cells.append(value)
                    continue
                # Limit escapes to the common TOON string grammar.
                if any(ord(c) < 32 and c not in "\r\n\t" for c in value):
                    return None
            cells.append(json.dumps(value, ensure_ascii=False))
        lines.append("  " + ",".join(cells))
    return "\n".join(lines)


def json_views(text, fields):
    data = json.loads(
        text,
        object_pairs_hook=unique_object,
        parse_float=NumberLiteral,
        parse_int=integer,
        parse_constant=invalid_constant,
    )
    # Preserve exact JSON number/string lexemes in a full view.
    compact = JSON_PART.sub(lambda m: m[0] if m[0][0] == '"' else "", text)
    yield compact
    if not (
        isinstance(data, list) and data and all(isinstance(r, dict) for r in data) and portable(data)
    ):
        return
    forms = [("", data)]
    reduced = project(data, set(fields))
    if reduced:
        note, rows = reduced
        yield note + json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
        forms.append(reduced)
    for note, rows in forms:
        encoded = table(rows)
        if encoded is not None:
            yield note + encoded


def git_view(path, data, commit):
    if not SHA.fullmatch(commit):
        raise ValueError("--base требует полный хеш ранее проверенного коммита Git.")
    if path.is_symlink():
        return None

    def git(*args, cwd=path.parent, **kwargs):
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, timeout=10, **kwargs)

    try:
        if git("cat-file", "-t", commit).stdout.strip() != b"commit":
            return None
        entry = git("--literal-pathspecs", "ls-tree", "-z", commit, "--", path.name)
        if entry.returncode or not entry.stdout.startswith((b"100644 blob ", b"100755 blob ")):
            return None
        blob = git("cat-file", "blob", f"{commit}:./{path.name}")
        if blob.returncode or b"\0" in blob.stdout:
            return None
        blob.stdout.decode("utf8")
        header = f"Base {commit} (same path).\n"
        if blob.stdout == data:
            return header + "Unchanged, byte-for-byte.\n"
        # Compare exactly the bytes we read. Ignore textconv, attributes and
        # external diff drivers, including filters in the user's repository.
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "before").write_bytes(blob.stdout)
            (tmp / "after").write_bytes(data)
            env = dict(
                os.environ,
                GIT_CONFIG_NOSYSTEM="1",
                GIT_CONFIG_GLOBAL=os.devnull,
                GIT_ATTR_NOSYSTEM="1",
                GIT_CEILING_DIRECTORIES=str(tmp.parent),
                LC_ALL="C",
            )
            patch = git(
                "-c",
                "core.autocrlf=false",
                "diff",
                "--no-index",
                "--no-color",
                "--text",
                "--no-ext-diff",
                "--no-textconv",
                "--unified=2",
                "--",
                "before",
                "after",
                cwd=tmp,
                env=env,
            )
        if patch.returncode == 1 and b"\n@@ " in patch.stdout:
            return header + patch.stdout.decode("utf8")
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        pass
    return None


def render(path, fields=(), base=None, *, receipt=None, start=None, lines=None):
    if start is not None or lines is not None:
        if fields or base is not None or receipt is not None:
            raise ValueError("Диапазон строк относится к исходнику. Выберите один способ чтения.")
        return source_page(path, 1 if start is None else start, 20 if lines is None else lines)
    if receipt is not None:
        if not isinstance(receipt, str) or not re.fullmatch(r"new|[0-9a-f]{64}", receipt):
            raise ValueError("Expected new or a complete view receipt.")
        before = signature(path)
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError(f"Файл больше {MAX_INPUT_BYTES // (1024 * 1024)} МБ. Откройте фрагмент через --find.")
    raw = path.read_bytes()
    text = raw.decode("utf8")
    if "\0" in text:
        raise ValueError("Нужен текстовый файл UTF-8.")
    content = text.removeprefix("\ufeff")
    candidates = []
    if path.suffix.lower() == ".json" or content.lstrip().startswith(("[", "{")):
        try:
            candidates.extend(json_views(content, fields))
        except json.JSONDecodeError:
            if path.suffix.lower() == ".json" or (fields and not base):
                raise
            # Other text formats (logs, INI) may also begin with a bracket.
    if base:
        delta = git_view(path, raw, base)
        if delta:
            candidates.append(delta)
    result = choose(text, candidates) if candidates else text
    encoded = raw if result is text else result.encode("utf8")
    if len(encoded) > RENDER_CAP:
        # Обрезаем по целым строкам: середина строки бесполезна для чтения,
        # а точный фрагмент и так добывается через --find.
        lines = result.splitlines(keepends=True)
        kept, used = [], 0
        for line in lines:
            size = len(line.encode("utf8"))
            if used + size > RENDER_CAP:
                break
            kept.append(line)
            used += size
        omitted = len(lines) - len(kept)
        head = "".join(kept).rstrip("\r\n")
        result = (head + f"\n[… показаны первые {len(kept)} из {len(lines)} строк "
                  f"({used} байт из {len(encoded)}) … "
                  f"REVIEW: пропущено строк: {omitted}. Для точного фрагмента используйте --find]")
    if receipt is not None:
        # Bind both the entire source (including cropped lines) and the exact representation.
        identity = ["view-v1", str(path.resolve()), list(fields), base, hashlib.sha256(raw).hexdigest(),
                    hashlib.sha256(result.encode("utf8")).hexdigest()]
        digest = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode("ascii")).hexdigest()
        if signature(path) != before:
            raise ValueError("Файл изменился во время чтения.")
        return "UNCHANGED\n" if receipt == digest else f"receipt={digest}\n{result}"
    return result


def fingerprint(path):
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return digest


def source_encoding(path):
    with path.open("rb") as probe:
        bom = probe.read(4)
    if bom.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        return "utf-32"
    if bom.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    return "utf-8-sig"


def source_page(path, start, lines):
    if type(start) is not int or start < 1 or type(lines) is not int or not 1 <= lines <= 100:
        raise ValueError("Начальная строка должна быть положительной, число строк от 1 до 100.")
    parts = []
    used = 0
    # Read even a single minified line in bounded chunks. Stop after this page.
    with path.open(encoding=source_encoding(path)) as stream:
        for number, prefix in enumerate(iter(lambda: stream.readline(441), ""), 1):
            if number >= start + lines:
                parts.append(f"Продолжение: --start {number}.")
                break
            if "\0" in prefix:
                raise ValueError("Нужен текстовый файл.")
            clipped = len(prefix) == 441 and not prefix.endswith("\n")
            if number >= start:
                line = prefix[:440].rstrip("\n") + (" […]" if clipped else "")
                row = f"{number} | {line}"
                size = len(row.encode("utf8")) + 1
                if used + size > RENDER_CAP:
                    parts.append(f"Продолжение: --start {number}.")
                    break
                parts.append(row)
                used += size
            if clipped:
                for chunk in iter(lambda: stream.readline(64 * 1024), ""):
                    if "\0" in chunk:
                        raise ValueError("Нужен текстовый файл.")
                    if chunk.endswith("\n"):
                        break
    return "\n".join(parts or [f"Начиная со строки {start} данных нет."]) + "\n"


def excerpts(path, term, start, limit, *, count_only=False):
    if not isinstance(term, str) or not term.strip() or "\n" in term or "\r" in term:
        raise ValueError("Укажите фрагмент одной строки.")
    if type(start) is not int or start < 1 or type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("Номер строки должен быть положительным, лимит совпадений от 1 до 20.")
    if type(count_only) is not bool:
        raise ValueError("count_only must be a boolean.")
    previous = deque(maxlen=2)
    shown = {}
    hits = tail_until = next_start = 0
    clipped = False
    with path.open(encoding=source_encoding(path)) as stream:
        for number, raw in enumerate(stream, 1):
            if "\x00" in raw:
                raise ValueError("Для этого файла откройте документ в его приложении.")
            if number < start - 2:
                continue
            if count_only:
                if number >= start and term in raw:
                    hits += 1
                continue
            position = raw.find(term)
            if position >= 0 and number >= start:
                hits += 1
                if hits <= limit:
                    shown.update(previous)
                    tail_until = number + 2
                elif not next_start:
                    next_start = number
            # Keep counting all matches and checking the encoding after the page is full.
            if hits >= limit and number > tail_until:
                continue
            line = raw.rstrip("\r\n")
            left = max(0, position - 80) if len(line) > 440 and position >= 0 else 0
            snippet = ("[…]" if left else "") + line[left:left+440]
            if len(line) > left + 440:
                snippet += "[…]"
            item = (snippet, len(line) > 440)
            if number <= tail_until:
                shown[number] = item
            previous.append((number, item))
    parts = [f"Совпадающих строк с {start}: {hits}."]
    for n, (line, shortened) in sorted(shown.items()):
        parts.append(f"{n} | {line}")
        clipped |= shortened
    if next_start:
        parts.append(f"Продолжение: --start {next_start}.")
    if clipped:
        parts.append("Длинные строки показаны частично.")
    return "\n".join(parts) + "\n", 0 if hits else 1


def signature(path):
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument(
        "--fields", nargs="+", default=[], help="Все поля, нужные для конкретного вопроса."
    )
    parser.add_argument("--base", help="Полный хеш уже проверенного коммита с этим файлом.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fingerprint", action="store_true", help="Показать SHA-256 байтов файла любого формата.")
    mode.add_argument("--expect-sha256", metavar="SHA256", help="Сверить байты с ранее проверенной версией.")
    mode.add_argument("--find", metavar="TEXT", help="Найти точный текст и показать по две соседние строки.")
    mode.add_argument("--receipt", help="new при первом чтении или код ранее полученного представления.")
    mode.add_argument("--lines", type=int, help="Показать 1–100 строк исходника с номерами.")
    parser.add_argument("--start", type=int, help="Первая строка поиска или страницы (по умолчанию 20 строк на страницу).")
    parser.add_argument("--limit", type=int, help="Число совпадений на страницу, по умолчанию 5.")
    parser.add_argument("--count", action="store_true", help="Показать только число строк с искомым текстом.")
    args = parser.parse_args()
    if not args.file.is_file():
        parser.error("Нужен существующий обычный файл.")
    compare = args.expect_sha256 is not None
    if compare and not re.fullmatch(r"[A-Fa-f0-9]{64}", args.expect_sha256):
        parser.error("Ожидался полный SHA-256.")
    if (args.fingerprint or compare or args.find is not None) and (args.fields or args.base):
        parser.error("Выберите один способ чтения файла.")
    if args.find is None and (args.limit is not None or args.count):
        parser.error("--limit и --count применяются при поиске через --find.")
    if args.start is not None and (args.fingerprint or compare):
        parser.error("--start применяется при чтении строк.")
    try:
        before = signature(args.file)
        status = 0
        if args.fingerprint or compare:
            digest = fingerprint(args.file)
            status = int(compare and digest != args.expect_sha256.lower())
            label = ("CHANGED " if status else "UNCHANGED ") if compare else ""
            result = f"{label}sha256={digest} bytes={before[2]}\n"
        elif args.find is not None:
            result, status = excerpts(args.file, args.find,
                                      1 if args.start is None else args.start,
                                      5 if args.limit is None else args.limit, count_only=args.count)
        else:
            result = render(args.file.absolute(), args.fields, args.base, receipt=args.receipt,
                            start=args.start, lines=args.lines)
        if signature(args.file) != before:
            raise ValueError("Файл изменился во время чтения.")
        sys.stdout.buffer.write(result.encode("utf8"))
        return status
    except (OSError, ValueError, RecursionError) as error:
        parser.exit(2 if (args.find is not None or compare or args.fingerprint) else 1,
                    f"Не удалось прочитать файл: {error}\n")


if __name__ == "__main__":
    sys.exit(main())
