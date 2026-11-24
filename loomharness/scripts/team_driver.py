"""Драйверы выдачи карточек через headless-режимы CLI и детектор моделей.

`run` выполняет одну выданную карточку выбранным CLI в папке проекта: собирает
промпт из доски команды, запускает headless-команду, полный вывод сохраняет в
.loomharness/runs/, на stdout возвращает сводку с использованием токенов, если
CLI её отдал в JSON.

`models` опрашивает установленные CLI и возвращает кандидатов в модели;
`models --apply --routine ИМЯ` сохраняет быструю модель проекта в
.codex/harness-models.json (её допуск решают слепые испытания team_models.py).

Подтверждённые документацией флаги: claude -p/--model/--output-format json;
gemini -p/--output-format json. Помеченные experimental шаблоны
(antigravity/opencode/dsh) сверьте с доками своей версии перед боевым запуском.
"""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import time

try:
    import hk
except ImportError:  # python -I исключает каталог скрипта из sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import hk


BINARY = {"claude": "claude", "gemini": "gemini", "antigravity": "antigravity",
          "opencode": "opencode", "dsh": "dsh"}
MODEL_RE = re.compile(r"^[\w.:/-]{1,80}$")


def decode_cli(data):
    """Декодируем вывод CLI: utf-8, при неудаче — локальная кодировка консоли."""
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        import locale
        return data.decode(locale.getpreferredencoding(False), errors="replace")


def kill_tree(proc):
    """Убить всё дерево процессов, а не только прямого ребёнка."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True, timeout=15)
    else:
        import signal
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()


def argv_for(cli, prompt, model):
    model_args = ["--model", model] if model and cli in ("claude", "gemini", "antigravity") else []
    if cli == "claude":      # документировано: claude -p "запрос" --output-format json
        return ["claude", "-p", prompt, *model_args, "--output-format", "json"]
    if cli == "gemini":      # документировано: gemini -p/--prompt, --output-format json
        return ["gemini", "-p", prompt, *model_args, "--output-format", "json"]
    if cli == "antigravity":  # experimental: наследует интерфейс Gemini
        return ["antigravity", "-p", prompt, *model_args, "--output-format", "json"]
    if cli == "opencode":     # experimental: opencode run "запрос"
        return ["opencode", "run", prompt]
    if cli == "dsh":          # experimental: dsh --profile headless "задача"
        return ["dsh", "--profile", "headless", prompt]
    raise ValueError(f"unknown driver cli: {cli}")


def parse_usage(cli, stdout):
    # JSON с использованием ищем от конца вывода: берём последнее удачное
    # дерево и извлекаем известные поля, не доверяя одному формату.
    text = (stdout or "").strip()
    for candidate in reversed(text.splitlines()):
        candidate = candidate.strip()
        if not candidate.startswith("{"):
            continue
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        if cli == "claude":
            usage = data.get("usage") or {}
            per_model = {k: v for k, v in usage.items() if isinstance(v, dict)}
            return {"cost_usd": data.get("total_cost_usd"), "per_model": per_model or None}
        stats = data.get("stats") or data.get("usage") or {}
        if isinstance(stats, dict) and stats:
            return {"stats": stats}
        if data.get("total_cost_usd") is not None:
            return {"cost_usd": data.get("total_cost_usd")}
    return None


def card_prompt(card, project, brief_deps, reply_language=None):
    parts = [
        f"Project: {project}", f"Goal: {card['goal']}",
        f"Card {card['id']} ({card['role']}): {card['title']}",
        f"Write scope: {card['scope']}", f"Acceptance: {card['criterion']}", f"Estimate: {card['hours']} h",
    ]
    if brief_deps:
        parts.append("Accepted dependencies: " + ", ".join(f"#{d['id']}" for d in brief_deps))
    parts.append("Work in the project within the write scope. Summarize changes, checks, and remaining risks.")
    parts.append(f"User-facing output language: {reply_language}." if reply_language else
                 "Use the user's requested language for the reply, otherwise the language of the task.")
    return "\n".join(parts)


def run_card(a):
    root = Path(a.project).resolve(strict=True)
    harness = hk.load("harness")
    board = harness["team_board"](root)
    if not board.is_file():
        raise ValueError("no team board for this project")
    with sqlite3.connect(board.resolve().as_uri() + "?mode=ro", uri=True, timeout=8) as db:
        db.row_factory = sqlite3.Row
        task = db.execute("SELECT * FROM tasks WHERE id=?", (a.task,)).fetchone()
        if not task or task["ticket"] != a.ticket or task["state"] not in ("reserved", "working"):
            raise ValueError("invalid dispatch ticket")
        project = db.execute("SELECT goal FROM project").fetchone()
        deps = [dict(r) for r in db.execute(
            "SELECT d.id, d.state FROM deps JOIN tasks d ON d.id = deps.needs WHERE deps.task=?",
            (a.task,))]
    card = {"id": task["id"], "role": task["role"], "title": task["title"], "scope": task["scope"],
            "criterion": task["criterion"], "hours": task["hours"], "goal": project["goal"]}
    prompt = card_prompt(card, root, deps, a.reply_language)

    log_dir = root / ".loomharness" / "runs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"task-{a.task}-{a.ticket}.log"
    argv = argv_for(a.cli, prompt, a.model)
    # npm-CLI на Windows ставятся .cmd/.bat-шимами: CreateProcess их не берёт,
    # поэтому разрешаем имя через PATH и батчники гоняем через cmd /c.
    resolved = shutil.which(argv[0])
    if resolved:
        argv[0] = resolved
    via_cmd = os.name == "nt" and argv[0].lower().endswith((".cmd", ".bat"))
    if via_cmd:
        # cmd.exe разбивает командную строку по переводам строк — сворачиваем
        # их в маркер, иначе промпт обрежется до первой строки. Нативные
        # .exe получают многострочный промпт как есть.
        argv = [a.replace("\n", " ¶ ") if isinstance(a, str) else a for a in argv]
        # cmd /c снимает первую и последнюю кавычку командной строки: оборачиваем
        # всё в двойные кавычки, чтобы внутренние кавычки аргументов уцелели.
        command = 'cmd /c "' + subprocess.list2cmdline(argv) + '"'
    else:
        command = argv
    # Отдельная группа процессов: при таймауте убиваем всё дерево, иначе
    # внуки удерживают пайпы и communicate() ждёт их естественного конца.
    popen_kw = {"cwd": str(root), "stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
    if os.name == "nt":
        popen_kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kw["start_new_session"] = True
    start = time.time()
    proc = subprocess.Popen(command, **popen_kw)
    try:
        raw_out, raw_err = proc.communicate(timeout=a.timeout)
        status = proc.returncode
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        raw_out, raw_err = proc.communicate()
        status = 124
        raw_err += f"\nTIMEOUT after {a.timeout}s (process tree terminated)".encode()
    seconds = round(time.time() - start, 3)
    out, err = decode_cli(raw_out), decode_cli(raw_err)
    with log_path.open("w", encoding="utf8") as handle:
        handle.write("$ " + " ".join(argv[:2]) + " …\n\n== stdout ==\n")
        handle.write(out or "")
        handle.write("\n== stderr ==\n")
        handle.write(err or "")
    print(json.dumps({"ok": status == 0, "exit": status, "cli": a.cli, "model": a.model,
                      "task": a.task, "ticket": a.ticket, "seconds": seconds,
                      "log": str(log_path), "usage": parse_usage(a.cli, out)}, ensure_ascii=False))


def settings_models(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    name = data.get("model", {}) if isinstance(data, dict) else {}
    value = name.get("name") if isinstance(name, dict) else name
    return [value] if isinstance(value, str) and value else []


def codex_model():
    config = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml"
    try:
        import tomllib
        data = tomllib.loads(config.read_text(encoding="utf8"))
    except (OSError, ValueError, ImportError):
        return []
    model = data.get("model") if isinstance(data, dict) else None
    return [model] if isinstance(model, str) and model else []


def opencode_model():
    for name in ("opencode.json", "opencode.jsonc"):
        path = Path.home() / ".config" / "opencode" / name
        if not path.is_file():
            continue
        match = re.search(r'"model"\s*:\s*"([^"]+)"', path.read_text(encoding="utf-8-sig", errors="replace"))
        if match:
            return [match.group(1)]
    return []


def detect_models():
    home = Path.home()
    gemini_settings = home / ".gemini" / "settings.json"
    report = {
        "claude": {"present": bool(shutil.which("claude")),
                   "models": ["sonnet", "opus", "haiku"] +
                             ([os.environ["ANTHROPIC_MODEL"]] if os.environ.get("ANTHROPIC_MODEL") else [])},
        "gemini": {"present": bool(shutil.which("gemini")), "models": settings_models(gemini_settings)},
        "antigravity": {"present": bool(shutil.which("antigravity")), "models": settings_models(gemini_settings)},
        "codex": {"present": bool(shutil.which("codex")), "models": codex_model()},
        "opencode": {"present": bool(shutil.which("opencode")), "models": opencode_model()},
        "dsh": {"present": bool(shutil.which("dsh")), "models": []},
    }
    return report


def models_command(a):
    if a.apply:
        if not a.routine or not MODEL_RE.match(a.routine):
            raise ValueError("укажите --routine с именем модели (буквы, цифры, . : / - _)")
        target = Path(a.project).resolve(strict=True) / ".codex" / "harness-models.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps({"routine": a.routine}, ensure_ascii=False, indent=2) + "\n", encoding="utf8")
        tmp.replace(target)
        print(f"routine model записана в {target}: {a.routine}", file=sys.stderr)
    print(json.dumps(detect_models(), ensure_ascii=False, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    cmds = parser.add_subparsers(dest="cmd", required=True)
    run = cmds.add_parser("run", help="выполнить выданную карточку выбранным CLI")
    run.add_argument("--cli", choices=tuple(BINARY), required=True)
    run.add_argument("--model", default=None)
    run.add_argument("--reply-language", help="user's requested output language for this card")
    run.add_argument("--project", type=Path, required=True)
    run.add_argument("--task", type=int, required=True)
    run.add_argument("--ticket", required=True)
    run.add_argument("--timeout", type=int, default=900)
    models = cmds.add_parser("models", help="опросить установленные CLI о доступных моделях")
    models.add_argument("--project", type=Path, default=Path.cwd())
    models.add_argument("--apply", action="store_true", help="записать --routine в .codex/harness-models.json проекта")
    models.add_argument("--routine", help="имя быстрой модели для --apply")
    args = parser.parse_args(argv)
    if args.cmd == "run":
        run_card(args)
    else:
        models_command(args)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass  # встраиваемые интерпретаторы без reconfigure
    try:
        main()
    except (OSError, ValueError, sqlite3.Error) as error:
        sys.exit(str(error))
