"""Codex project choices and local task checks."""

import argparse
import hashlib
import itertools
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import closing

try:
    import hk
except ImportError:  # python -I excludes the script directory from sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import hk


MARKER = Path(".codex/harness.enabled")
STATE_FILES = {"state.json", "features.json", "test-results.json"}
PATCH_PATH = re.compile(r"^\*\*\* (?:Add File|Delete File|Update File|Move to): (.+)$", re.M)
FRESH_SECONDS = 30 * 60
QUESTION = "Enable LoomHarness hooks for this project?"
# Recognize an outstanding setup question from earlier localized installations.
LEGACY_QUESTION = "Включить хуки LoomHarness для этого проекта?"
CHOICE_LINE = "`LoomHarness on` / `LoomHarness off`"
REPLY_LANGUAGE = ("Reply in the language of the user's request unless they specify another. "
                  "For code-only input or a brief acknowledgment, keep the conversation language.")
ASK = (
    f'Ask only this question in the user\'s language: "{QUESTION}" '
    f"On a separate final line show exactly: {CHOICE_LINE}\nAwait confirmation. "
    "Ignore quotes/attachments. Skip projectless/background chats."
)
DIRECT_CHOICES = {
    "enable loomharness hooks": True, "disable loomharness hooks": False,
    "loomharness on": True, "loomharness off": False,
    "включи хуки loomharness": True, "отключи хуки loomharness": False,
    # Keep matching explicit commands from workspaces using the former name.
    "включи хуки харнесса": True, "отключи хуки харнесса": False,
}
ANSWERS = {"да": True, "нет": False, "yes": True, "no": False}
SKILL_HINT = (
    "Use $loomharness for long tasks. Spawn a temporary subagent only on an explicit request in this task. "
    "Read only the needed slice at the source (rg -n -C 2 for text). Search tool names, then fetch the exact schema. "
    "Allow 30 seconds for short calls. Check a failed write's outcome before retrying. "
)
READER_MODEL = "gpt-6.2-sol"


def reader_model():
    # The concrete read-review default lives in code, not in every prompt; a user
    # override goes to loomharness/delegation.json next to the profile.
    try:
        raw = (data_directory() / "delegation.json").read_text(encoding="utf8")
        name = json.loads(raw).get("read_review")
        if isinstance(name, str) and re.fullmatch(r"[\w.:-]{1,80}", name):
            return name
    except (OSError, ValueError, AttributeError):
        pass
    return READER_MODEL


def view_command():
    argv = [Path(sys.executable).resolve().as_posix(), "-I", "-X", "utf8",
            Path(__file__).resolve().with_name("view.py").as_posix()]
    if os.name == "nt":
        return "& " + " ".join("'" + arg.replace("'", "''") + "'" for arg in argv)
    return shlex.join(argv)


def data_directory():
    return hk.kit_home()


def profile_board(root):
    key = hashlib.sha256(os.fsencode(os.path.normcase(str(root.resolve())))).hexdigest()
    profile = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    return hk.kit_home(profile) / "teams" / (key + ".sqlite3")


def team_board(root):
    # Hooks, direct CLI calls and launchers all enter through the same profile name.
    # Resolve it before opening SQLite so every caller uses the same WAL and lock files.
    return profile_board(root).resolve()


def board_storage(root, bind_workspace):
    entry = profile_board(root)
    local = root.resolve() / ".loomharness" / "data" / "teams" / entry.name
    if bind_workspace:
        if local.exists() and entry.is_file() and not local.samefile(entry):
            raise ValueError("two existing boards; inspect their tasks before choosing a location")
        if not entry.is_symlink() and not entry.exists():
            local.parent.mkdir(parents=True, exist_ok=True)
            entry.parent.mkdir(parents=True, exist_ok=True)
            # Creating the alias competes atomically with a concurrent first init. Never replace a database.
            try:
                entry.symlink_to(local)
            except FileExistsError:
                pass
    path = team_board(root)
    if bind_workspace and local.is_file() and path.is_file() and not local.samefile(path):
        raise ValueError("two existing boards; inspect their tasks before choosing a location")
    return {"location": "workspace" if path == local.resolve() else "profile",
            "path": str(path), "exists": path.is_file()}


def board_settings(root):
    path = team_board(root)
    if not path.is_file():
        return False, None
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as team:
        names = {r[0] for r in team.execute("SELECT name FROM sqlite_master WHERE name IN ('project','git_policy')")}
        policy = team.execute("SELECT mode,directory FROM git_policy WHERE id=1").fetchone() if "git_policy" in names else None
        return "project" in names, policy


def selected_git_status(root, board=None):
    _, policy = board if board is not None else board_settings(root)
    if policy and policy[0] == "none":
        return ""
    target = Path(policy[1]) if policy else root
    return git_status(target) if target.is_dir() and in_git_worktree(target) else ""


def open_preferences():
    base = data_directory()
    base.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(base / "projects.sqlite3", timeout=3)
    db.execute("CREATE TABLE IF NOT EXISTS projects (root TEXT PRIMARY KEY, enabled INTEGER NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS questions (session TEXT PRIMARY KEY, root TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS delegation_notices (receipt BLOB PRIMARY KEY) WITHOUT ROWID")
    return db


def project_choice(db, cwd, *, reading_from_subdir=False):
    # Hook cwd is the session's working directory. Two selected non-Git folders
    # are separate projects, even when one is nested inside the other.
    scope = cwd
    directories = []
    for directory in (cwd, *cwd.parents):
        directories.append(directory)
        if (directory / ".git").exists():
            scope = directory
            break
    for directory in directories:
        legacy = (directory / MARKER).is_file()
        if directory != scope and not legacy and not reading_from_subdir:
            continue
        if db is not None:
            row = db.execute("SELECT enabled FROM projects WHERE root=?", (os.path.normcase(str(directory)),)).fetchone()
            if row is not None:
                return directory, bool(row[0])
        if legacy:
            return directory, True
    return scope, None


def context(event_name, message):
    return {"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": message}}


def session_hint(db, root, session, *, only_if_new=False, board=None):
    if (root / "AGENT_STOP").exists():
        return "AGENT_STOP: stop work and report status in the user's language."
    has_team, policy = board if board is not None else board_settings(root)
    if session:
        version = ("team-org-v8" if has_team else "project-v12") + ("-git" if policy else "")
        receipt = hashlib.sha256((version + "\0" + session + "\0" + os.path.normcase(str(root))).encode("utf8")).digest()
        inserted = db.execute("INSERT OR IGNORE INTO delegation_notices VALUES (?)", (receipt,)).rowcount
        if only_if_new and not inserted:
            return None
    if has_team:
        team_hint = ("Run team.py intake to find your role. Handle short tasks here. Reuse the subproject's chat "
                     "for its next card. Workers claim tickets with claim or job-claim and report blockers "
                     "to the coordinator at turn end. The coordinator asks the owner. Use inbox --next; wake reviewers on a submitted SHA. ")
    else:
        team_hint = ("Handle short tasks here. While agents run, take one independent planned step or wait. "
                     "Resume reviewers on a submitted revision (references/team.md). "
                     "For a new team, use the interactive model-choice flow in references/adaptive-team.md. ")
    git_hint = ""
    if not policy:
        git_hint = "At first planning, confirm the project's Git choice and save it with team.py git-choice. "
    return (REPLY_LANGUAGE + " " + team_hint + git_hint + SKILL_HINT +
            f"For a requested read-only agent, send the criterion and relevant files. Default: {reader_model()}, low, fork_turns=none. "
            "Append the file path to the command below. Use --start/--lines for lines, --find for text, "
            "--fingerprint for SHA-256, --fields for JSON fields.\n"
            + view_command() + "\n")


def choice_message(enabled):
    return "LoomHarness hooks enabled for this project." if enabled else "LoomHarness hooks disabled for this project."


def is_setup_question(message):
    # Other translations offer the same two explicit commands. A bare yes/no
    # must refer to a recognized question, never to a quote or another prompt.
    question = message.strip().removesuffix("\n" + CHOICE_LINE).strip()
    return question.strip("*«»\"").strip() in (QUESTION, LEGACY_QUESTION)


def save_choice(db, root, enabled):
    key = os.path.normcase(str(root))
    db.execute("INSERT INTO projects VALUES (?, ?) ON CONFLICT(root) DO UPDATE SET enabled=excluded.enabled",
               (key, enabled))


def history_notice(db, root, session, client, usage):
    if not usage:
        return None
    try:
        decision = hk.load("history")["budget"](**usage)
    except (ValueError, TypeError):
        return None
    if decision["action"] == "measure":
        return None
    identity = hashlib.sha256(json.dumps([str(root), session, client]).encode()).digest()
    db.execute("CREATE TABLE IF NOT EXISTS history_notices (session BLOB PRIMARY KEY, policy TEXT NOT NULL)")
    if decision["action"] != "compact":
        db.execute("DELETE FROM history_notices WHERE session=?", (identity,))
        return None
    policy = f"{usage['window_tokens']}/{decision['trigger_tokens']}"
    changed = db.execute("INSERT INTO history_notices VALUES (?, ?) ON CONFLICT(session) DO UPDATE "
                         "SET policy=excluded.policy WHERE policy!=excluded.policy", (identity, policy)).rowcount
    if changed:
        why = ("Make room for the next reply." if decision["reason"] == "context_window_pressure"
               else f"Compaction pays off at call {decision['break_even_calls']}.")
        return (f"Context reached {decision['trigger_tokens']} tokens. {why} Target {decision['target_tokens']}. "
                "Keep requirements, open questions, and source links. Use the client's compaction method "
                "in references/history.md and check the next input size.")
    return None


def prompt_submitted(db, cwd, session, prompt, *, client=None, usage=None):
    name = "UserPromptSubmit"
    answer = " ".join(prompt.casefold().split()).rstrip(".!")
    # A bare yes/no is eligible only directly after our own question in this chat.
    # Serialize the lookup and the answer so an older chat cannot undo a saved choice.
    db.execute("BEGIN IMMEDIATE")
    root, enabled = project_choice(db, cwd)
    key = os.path.normcase(str(root))
    pending = db.execute("SELECT root FROM questions WHERE session=?", (session,)).fetchone()
    db.execute("DELETE FROM questions WHERE session=?", (session,))
    if answer in DIRECT_CHOICES:
        enabled = DIRECT_CHOICES[answer]
    elif pending == (key,) and answer in ANSWERS:
        if enabled is None:
            enabled = ANSWERS[answer]
    else:
        if pending == (key,) and enabled is None:
            return context(name, "Interpret the user's own reply. Run enable or disable only for a clear choice. "
                           "Otherwise clarify in the user's language.")
        # An already-open chat may still have the previous release's startup instructions.
        hint = session_hint(db, root, session, only_if_new=True) if enabled else None
        db.commit()
        parts = [hint] if hint else []
        if usage and enabled and not (root / "AGENT_STOP").exists():
            if notice := history_notice(db, root, session, client, usage):
                parts.append(notice)
        if enabled and len(prompt) <= 120 and not (root / "AGENT_STOP").exists():
            # Codex invokes the helper with -I. Load only the installed sibling.
            # Plan excerpts are an optimization; a failure here must not break the hook.
            try:
                plan = hk.load("plan")
                if excerpts := plan["for_prompt"](root, prompt):
                    parts.append(excerpts)
            except (OSError, ValueError, KeyError, TypeError):
                pass
        return context(name, "\n".join(parts)) if parts else None
    save_choice(db, root, enabled)
    message = ("Project hooks " + ("enabled." if enabled else "disabled.") +
               " Briefly confirm in the user's language and continue their task.")
    if enabled:
        message += " " + session_hint(db, root, session)
    return context(name, message)


def find_git():
    directories = [Path(p) for p in os.get_exec_path() if os.path.isabs(p)]
    if os.name == "nt":
        directories.append(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/cmd")
    program = "git.exe" if os.name == "nt" else "git"
    for directory in directories:
        executable = directory / program
        if executable.is_file() and os.access(executable, os.X_OK):
            return executable
    return None


def in_git_worktree(root):
    return bool(os.environ.get("GIT_DIR")) or any((p / ".git").exists() for p in (root, *root.parents))


GIT_STATUS_TTL = 60.0


def git_stamps(root):
    # Cheap cache invalidation: init, commit, checkout or staging touches one of
    # these mtimes. Caveat: editing the content of a tracked file does not change
    # any of them, so the changed-files counter can lag up to GIT_STATUS_TTL.
    stamps = []
    for candidate in (root, root / ".git", root / ".git" / "HEAD", root / ".git" / "index"):
        try:
            stamps.append(candidate.stat().st_mtime_ns)
        except OSError:
            stamps.append(None)
    return stamps


def git_status_cache():
    path = data_directory() / "git-status.json"
    try:
        data = json.loads(path.read_text(encoding="utf8"))
        return path, data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return path, {}


def write_git_status_cache(path, cache):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf8", dir=path.parent,
                                         delete=False) as handle:
            json.dump(cache, handle, ensure_ascii=False)
            temp = Path(handle.name)
        temp.replace(path)
    except OSError:
        try:
            temp.unlink(missing_ok=True)
        except (OSError, UnboundLocalError):
            pass


def run_git_status(git, root):
    try:
        result = subprocess.run(
            [str(git), "--no-optional-locks", "-c", "core.fsmonitor=false", "-C", str(root),
             "status", "--porcelain=v1", "--branch", "--untracked-files=normal"],
            capture_output=True, encoding="utf-8", errors="replace", timeout=6,
            env={**os.environ, "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return "Could not read Git status."
    if result.returncode:
        return "Outside a Git repository." if "not a git repository" in result.stderr else "Git status unavailable."
    lines = result.stdout.splitlines()
    if not lines:
        return "Git status unavailable."
    return f"Git: {lines[0].removeprefix('## ')}. Modified and new files: {len(lines) - 1}."


def git_status(root):
    git = find_git()
    if git is None:
        return "Git unavailable."
    key = os.path.normcase(str(root))
    stamps = git_stamps(root)
    cache_file, cache = git_status_cache()
    entry = cache.get(key)
    if entry and entry.get("stamps") == stamps and time.time() - entry.get("at", 0) < GIT_STATUS_TTL:
        return entry["message"]
    message = run_git_status(git, root)
    if len(cache) > 500:
        cache = {}
    cache[key] = {"stamps": stamps, "at": time.time(), "message": message}
    write_git_status_cache(cache_file, cache)
    return message


def receipt_path(root, session, *, create=False):
    key = os.path.normcase(str(root)) + "\0" + session
    name = "codex-harness-evidence"
    if os.name == "posix":
        name += f"-{os.getuid()}"
    directory = Path(tempfile.gettempdir()) / name
    if create:
        directory.mkdir(mode=0o700, exist_ok=True)
    if os.name == "posix":
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise PermissionError(f"Expected a private temporary directory with mode 0700: {directory}")
    return directory / hashlib.sha256(key.encode()).hexdigest()


def file_signature(stat):
    return [stat.st_size, stat.st_mtime_ns]


def cached_digest(path):
    # Review and delivery verify the submitted bytes even when a replacement
    # preserved size and mtime. Keep the entry point used by existing callers.
    return hk.load("view")["fingerprint"](path)


def configured_state_files(root):
    if not (root / ".codex/harness-plan.json").is_file():
        return set()
    names = hk.load("plan")["project_config"](root).get("state_files")
    if not isinstance(names, list):
        return set()
    paths = set()
    for name in names:
        if not isinstance(name, str) or not re.fullmatch(r"[\w ./-]{1,240}", name):
            continue
        if any(not part or part.startswith(".") for part in name.split("/")):
            continue
        try:
            path = (root / name).resolve()
        except (OSError, RuntimeError):
            continue
        if path.is_relative_to(root):
            paths.add(path)
    return paths


def read_evidence(root, args):
    session = os.environ.get("CODEX_THREAD_ID")
    if not session:
        raise ValueError("Run read from the active Codex project chat.")
    if (root / "AGENT_STOP").exists():
        raise ValueError("AGENT_STOP has paused activity.")
    path = Path(args.path).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("Select a regular evidence file inside the project directory.")
    if path.name.lower() in STATE_FILES | {"handoff.md", "status.md", "session.md"} or path in configured_state_files(root):
        raise ValueError("Select a verification report or the resulting artifact.")
    if args.find is not None and not args.find.strip():
        raise ValueError("Supply nonempty search text.")
    limit = args.lines if args.lines is not None else (12 if args.find is not None else 40)
    if args.start < 1 or not 1 <= limit <= 500:
        raise ValueError("Use a positive start line and a line count from 1 to 500.")
    next_match = None
    with path.open(encoding="utf-8-sig") as handle:
        before = file_signature(os.fstat(handle.fileno()))
        numbered = enumerate(itertools.islice(handle, args.start - 1, None), args.start)
        if args.find is None:
            lines = list(itertools.islice(numbered, limit))
        else:
            matches = ((number, line) for number, line in numbered if args.find in line)
            lines = list(itertools.islice(matches, limit))
            next_match = next(matches, None)
            if not lines:
                raise ValueError("No matches. Refine the query or read a line range.")
        if not any(s.strip() for _, s in lines) or any("\x00" in s for _, s in lines):
            raise ValueError("The excerpt is empty or contains binary data.")
        if before != file_signature(os.fstat(handle.fileno())):
            raise ValueError("The file changed during the read. Check it again.")
    clipped = False
    for number, line in lines:
        line = line.rstrip()
        if len(line) > 440:
            line = line[:435] + " […]"
            clipped = True
        print(f"{number}: {line}")
    if clipped:
        print("Long lines are truncated.")
    if next_match:
        print(f"More matches. Continue this search with --start {next_match[0]}.")
    sys.stdout.flush()
    receipt = receipt_path(root, session, create=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf8", dir=receipt.parent, delete=False) as handle:
        json.dump({"file": str(path), "signature": before, "at": time.time()}, handle)
    Path(handle.name).replace(receipt)


def fresh_evidence(root, session, protected):
    if not session:
        return False
    try:
        data = json.loads(receipt_path(root, session).read_text(encoding="utf8"))
        path = Path(data["file"]).resolve(strict=True)
        return (path.is_relative_to(root) and path.is_file() and path not in protected
                and 0 <= time.time() - data["at"] < FRESH_SECONDS
                and file_signature(path.stat()) == data["signature"])
    except (KeyError, OSError, ValueError, TypeError):
        return False


def deny(reason):
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


GATE_TOOLS = {"apply_patch", "edit_file"}


def patch_targets(cwd, tool_input):
    # Codex patches encode destinations in patch text. Other editors (Claude
    # Edit/Write/MultiEdit and Gemini write_file/replace) supply explicit
    # path fields, sometimes inside a list of edits.
    targets = []
    if not isinstance(tool_input, dict):
        return targets
    command = tool_input.get("command", "")
    if command:
        targets = [cwd / p.strip() for p in PATCH_PATH.findall(command)]
    else:
        for key in ("file_path", "path", "notebook_path"):
            value = tool_input.get(key)
            if isinstance(value, str) and value:
                targets.append(cwd / value)
        for edit in tool_input.get("edits") or []:
            if isinstance(edit, dict) and isinstance(edit.get("file_path"), str):
                targets.append(cwd / edit["file_path"])
    return targets


def on_event(db, event):
    cwd = Path(event["cwd"]).resolve()
    name = event["hook_event_name"]
    session = event.get("session_id", "")
    if name == "UserPromptSubmit" and session:
        return prompt_submitted(db, cwd, session, event.get("prompt", ""),
                                client=event.get("client"), usage=event.get("context_usage"))
    root, enabled = project_choice(db, cwd)
    if enabled is None:
        if name == "SessionStart":
            return context(name, ASK)
        if name == "Stop" and session and is_setup_question(event.get("last_assistant_message") or ""):
            db.execute("INSERT INTO questions VALUES (?, ?) ON CONFLICT(session) DO UPDATE SET root=excluded.root",
                       (session, os.path.normcase(str(root))))
        return None
    if not enabled:
        return None
    if name == "SessionStart":
        board = board_settings(root)
        hint = session_hint(db, root, session, only_if_new=True, board=board)
        if hint is None:
            return None
        status = selected_git_status(root, board=board)
        return context(name, hint + (" " + status if status else ""))
    if name == "Stop" and not event.get("stop_hook_active"):
        status = selected_git_status(root)
        return {"systemMessage": status} if status.startswith("Git:") else None
    if name != "PreToolUse":
        return None
    if (root / "AGENT_STOP").exists():
        return deny("AGENT_STOP: stop work and report status in chat.")
    if event.get("tool_name") in GATE_TOOLS:
        targets = patch_targets(cwd, event.get("tool_input", {}))
        protected = configured_state_files(root) if targets else set()
        touches_state = any(p.name.lower() in STATE_FILES or (protected and p.resolve() in protected)
                            for p in targets)
        if touches_state and not fresh_evidence(root, event.get("session_id"), protected):
            return deny("Before editing state, open the evidence with harness.py read in this chat.")
    return None


_NEEDS_DB = object()


def cheap_hook_result(event):
    # Most PreToolUse events are unrelated to state files. Without an
    # AGENT_STOP in cwd or its parents, the full handler returns None for these
    # events regardless of project choice. Avoid opening the database on this
    # frequent path.
    if event.get("hook_event_name") != "PreToolUse":
        return _NEEDS_DB
    if event.get("tool_name") in GATE_TOOLS:
        return _NEEDS_DB
    try:
        cwd = Path(event.get("cwd") or ".").resolve()
    except (OSError, ValueError):
        return _NEEDS_DB
    if (cwd / "AGENT_STOP").exists() or any((p / "AGENT_STOP").exists() for p in cwd.parents):
        return _NEEDS_DB
    return None


def run_hook(cli):
    # Normalize the client event, filter irrelevant calls, process the
    # canonical event and emit a client-specific decision.
    adapters = hk.load("cli_adapters")
    raw = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    event, cli = adapters["normalize"](raw, cli)
    quick = cheap_hook_result(event)
    if quick is not _NEEDS_DB:
        return
    with closing(open_preferences()) as db:
        with db:
            result = on_event(db, event)
        adapters["emit"](result, cli)
        if event.get("session_id") and event["hook_event_name"] in {"SessionStart", "UserPromptSubmit", "PreToolUse", "Stop"}:
            # Record only callbacks handled above. Ordinary PreToolUse keeps its DB-free path.
            try:
                with db:
                    db.execute("PRAGMA busy_timeout=0")
                    db.execute("""CREATE TABLE IF NOT EXISTS hook_events (
                        cwd TEXT, session TEXT, event TEXT, client TEXT, at REAL,
                        PRIMARY KEY(cwd, session)) WITHOUT ROWID""")
                    db.execute("INSERT OR REPLACE INTO hook_events VALUES(?,?,?,?,?)",
                               (os.path.normcase(str(Path(event["cwd"]).resolve())), event["session_id"],
                                event["hook_event_name"], cli, time.time()))
            except sqlite3.Error:
                pass  # A diagnostic receipt must not change a hook decision.


def loom_hook(hook):
    return (hook.get("statusMessage") or "").startswith(("LoomHarness · ", "Харнесс"))


def installed_hook_file():
    path = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "hooks.json"
    try:
        groups = json.loads(path.read_text(encoding="utf-8-sig"))["hooks"]
        events = [name for name, entries in groups.items()
                  if any(loom_hook(hook) for entry in entries for hook in entry.get("hooks", []))]
        return {"path": str(path), "status": "read", "events": events}
    except FileNotFoundError:
        return {"path": str(path), "status": "missing"}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        return {"path": str(path), "status": "unavailable", "reason": str(error)}


def hook_registry(cwd, executable):
    try:
        request = hk.load("context")["codex_request"]
        reply = request(cwd, "hooks/list", {"cwds": [str(cwd)]}, executable=executable)
        group = next((g for g in reply["data"] if Path(g["cwd"]) == cwd), None)
        if group is None:
            raise ValueError("Codex omitted the requested project directory")
        hooks = [{key: hook.get(key) for key in ("eventName", "enabled", "trustStatus", "source")}
                 for hook in group["hooks"]
                 if loom_hook(hook)]
        return {"status": "queried", "hooks": hooks, "warnings": group.get("warnings", []),
                "errors": group.get("errors", [])}
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        return {"status": "unavailable", "reason": str(error)}


def doctor(executable):
    import socket
    from datetime import datetime, timezone

    cwd = Path.cwd().resolve()
    session = os.environ.get("CODEX_THREAD_ID")
    prefs = data_directory() / "projects.sqlite3"
    root, enabled = project_choice(None, cwd)
    last = None
    if prefs.is_file():
        with closing(sqlite3.connect(prefs.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            root, enabled = project_choice(db, cwd)
            if session and db.execute("SELECT 1 FROM sqlite_master WHERE name='hook_events'").fetchone():
                row = db.execute("SELECT event,client,at FROM hook_events WHERE cwd=? AND session=?",
                                 (os.path.normcase(str(cwd)), session)).fetchone()
                if row:
                    last = {"event": row[0], "client": row[1],
                            "at": datetime.fromtimestamp(row[2], timezone.utc).isoformat()}
    print(json.dumps({"host": socket.gethostname(), "transport": "ssh" if os.environ.get("SSH_CONNECTION") else "local",
                      "cwd": str(cwd), "project": str(root), "session": session, "projectEnabled": enabled,
                      "userHookFile": installed_hook_file(), "codexRegistry": hook_registry(cwd, executable),
                      "lastHook": last}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("enable", "disable", "status"):
        commands.add_parser(name)
    diagnose = commands.add_parser("doctor", help="inspect project choice, Codex registry and latest observed callback")
    diagnose.add_argument("--codex", help="Codex executable used by the host application")
    hook = commands.add_parser("hook")
    hook.add_argument("--cli", choices=("auto", "portable", "codex", "claude", "gemini", "antigravity", "opencode", "dsh"),
                      default="auto",
                      help="Target CLI event and decision format (default auto)")
    hk.load("self_update")["add_parser"](commands)
    read = commands.add_parser("read")
    read.add_argument("path")
    read.add_argument("--start", type=int, default=1)
    read.add_argument("--lines", type=int, help="Maximum lines (default 40, or 12 when searching).")
    read.add_argument("--find", help="Literal text to find from --start.")
    args = parser.parse_args()
    if args.command == "update":
        sys.exit(hk.load("self_update")["main"](args))
    if args.command == "hook":
        run_hook(args.cli)
        return
    if args.command == "doctor":
        doctor(args.codex)
        return
    with closing(open_preferences()) as db, db:
        dispatch(db, args)


def dispatch(db, args):
    if args.command == "hook":
        run_hook(getattr(args, "cli", "auto"))
        return
    cwd = Path.cwd().resolve()
    root, enabled = project_choice(db, cwd, reading_from_subdir=args.command == "read")
    if args.command in {"enable", "disable"}:
        enabled = args.command == "enable"
        save_choice(db, root, enabled)
        print(choice_message(enabled), root)
        return
    if args.command == "status":
        print("Project choice:", "pending." if enabled is None else choice_message(enabled), root)
        print("Hook registration and events for this chat: harness.py doctor")
        if enabled and (root / "AGENT_STOP").exists():
            print("AGENT_STOP has paused activity.")
        _, policy = board_settings(root)
        print("Git disabled for this project." if policy and policy[0] == "none" else git_status(Path(policy[1]) if policy else root))
    elif enabled:
        read_evidence(root, args)
    else:
        raise ValueError("Enable project hooks before recording an evidence read.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, sqlite3.Error) as error:
        sys.exit(str(error))
