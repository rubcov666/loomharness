"""Aggregate measured usage from local Codex rollouts for registered project chats."""

from collections import OrderedDict, defaultdict
from contextlib import closing
from datetime import datetime
import json
import os
import re
from pathlib import Path
import sqlite3
from statistics import median
import time


# Illustrative weights for comparing the same tasks: cached input is cheaper
# than fresh input and output is more expensive. These weights are for local
# comparisons, not provider billing.
PRICE_INPUT = 1.0
PRICE_CACHED = 0.1
PRICE_OUTPUT = 4.0


def _cache_db():
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").resolve()
    kit = home / "loomharness"
    legacy = home / "harness-kit"
    if legacy.is_dir() and not kit.is_dir():
        kit = legacy
    path = kit / "usage-cache.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _freeze(value):
    if isinstance(value, set):
        return {"__set__": [_freeze(v) for v in value]}
    if isinstance(value, tuple):
        return {"__tuple__": [_freeze(v) for v in value]}
    if isinstance(value, list):
        return [_freeze(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _freeze(v) for k, v in value.items()}
    return value


def _thaw(value):
    if isinstance(value, dict):
        if set(value) == {"__set__"}:
            return {_thaw(v) for v in value["__set__"]}
        if set(value) == {"__tuple__"}:
            return tuple(_thaw(v) for v in value["__tuple__"])
        return {k: _thaw(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_thaw(v) for v in value]
    return value


def _turns_schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS turn_cache (
        path TEXT, thread TEXT, trial INTEGER, stamp TEXT, payload TEXT,
        PRIMARY KEY(path,thread,trial))""")


def _log_stamp(path):
    info = path.stat()
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _cache_lookup(path, stamp, thread, trial):
    try:
        with closing(sqlite3.connect(_cache_db(), timeout=3)) as db:
            _turns_schema(db)
            row = db.execute(
                "SELECT payload FROM turn_cache WHERE path=? AND thread=? AND trial=? AND stamp=?",
                (str(path), thread, int(trial), json.dumps(stamp))).fetchone()
            return json.loads(row[0]) if row else None
    except (OSError, sqlite3.Error, ValueError):
        return None


def _cache_store(path, stamp, thread, trial, turns):
    try:
        with closing(sqlite3.connect(_cache_db(), timeout=3)) as db, db:
            _turns_schema(db)
            db.execute("INSERT OR REPLACE INTO turn_cache VALUES(?,?,?,?,?)",
                       (str(path), thread, int(trial), json.dumps(stamp),
                        json.dumps(_freeze(dict(turns)), ensure_ascii=False)))
    except (OSError, sqlite3.Error, ValueError, TypeError):
        pass


def local_indexes(home):
    return sorted((f for f in home.glob("state_*.sqlite") if f.stem[6:].isdigit()),
                  key=lambda f: int(f.stem[6:]), reverse=True)


def rollout_paths(home, threads):
    # Use the live threads index, including committed rows still in its WAL.
    paths = {}
    wanted = set(threads)
    for file in local_indexes(home):
        pending = list(wanted - paths.keys())
        if not pending:
            break
        try:
            with closing(sqlite3.connect(file.resolve().as_uri() + "?mode=ro", uri=True)) as index:
                for start in range(0, len(pending), 500):
                    batch = pending[start:start + 500]
                    placeholders = ",".join("?" for _ in batch)
                    sql = f"SELECT id, rollout_path FROM threads WHERE id IN ({placeholders})"
                    for tid, rollout in index.execute(sql, batch):
                        if rollout and Path(rollout).is_file():
                            paths[tid] = Path(rollout)
        except (OSError, sqlite3.Error):
            continue
    return paths


def timestamp(value):
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if instant.utcoffset() is None:
        raise ValueError("timestamps need a timezone")
    return instant.timestamp()


def participant_paths(home, roots, end):
    # Use the newest index containing each root. UNION also deduplicates shared descendants.
    participants = {}
    indexed = set()
    pending = set(roots)
    for file in local_indexes(home):
        if not pending:
            break
        with closing(sqlite3.connect(file.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("BEGIN")
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='threads'").fetchone():
                continue
            edges = db.execute("SELECT 1 FROM sqlite_master WHERE name='thread_spawn_edges'").fetchone()
            has_created = any(row[1] == "created_at" for row in db.execute("PRAGMA table_info(threads)"))
            for root in sorted(pending):
                found = db.execute("SELECT rollout_path FROM threads WHERE id=?", (root,)).fetchone()
                if not found:
                    continue
                pending.remove(root)
                participants[root] = found[0]
                if edges:
                    indexed.add(root)
                    created = "WHERE t.created_at IS NULL OR t.created_at<=?" if has_created else ""
                    rows = db.execute(f"""WITH RECURSIVE family(id) AS (VALUES(?) UNION
                        SELECT child_thread_id FROM thread_spawn_edges e JOIN family ON family.id=e.parent_thread_id)
                        SELECT family.id,t.rollout_path FROM family LEFT JOIN threads t ON t.id=family.id {created}""",
                                      (root, end) if has_created else (root,))
                    participants.update(rows)
    for root in pending:
        participants[root] = None
    return participants, indexed == set(roots)


def window_usage(roots, since, until):
    start, end = timestamp(since), timestamp(until)
    if end <= start:
        raise ValueError("--until must be later than --since")
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").resolve()
    paths, indexed = participant_paths(home, roots, end)
    totals = dict(responses=0, input=0, cached_input=0, uncached_input=0, output=0)
    actors, missing = {}, []
    invalid = 0
    for thread, path in sorted(paths.items()):
        counts = dict.fromkeys(totals, 0)
        seen = set()
        try:
            if not path:
                raise FileNotFoundError
            with Path(path).open(encoding="utf-8") as log:
                for line in log:
                    if '"token_usage_record"' not in line:
                        continue
                    try:
                        item = json.loads(line)
                        if item.get("type") != "token_usage_record" or item["payload"].get("thread_id") != thread:
                            continue
                        if not start <= timestamp(item["timestamp"]) < end:
                            continue
                        data = item["payload"]
                        key, usage = data["response_id"], data["usage"]
                        i, c, o = (usage[k] for k in ("input_tokens", "cached_input_tokens", "output_tokens"))
                        if not key or not all(type(n) is int and n >= 0 for n in (i,c,o)) or c > i:
                            raise ValueError("invalid token counters")
                    except (KeyError, ValueError, TypeError, AttributeError):
                        invalid += 1
                        continue
                    if key in seen:
                        continue
                    seen.add(key)
                    counts["responses"] += 1
                    counts["input"] += i
                    counts["cached_input"] += c
                    counts["uncached_input"] += i - c
                    counts["output"] += o
        except (OSError, UnicodeError):
            missing.append(thread)
        for key, n in counts.items():
            totals[key] += n
        actors[thread] = counts
    return {"since": since, "until": until, "window_seconds": end-start, "participants": actors, "measured": totals,
            "descendants_indexed": indexed, "missing_chats": missing, "invalid_records": invalid,
            "complete": bool(totals["responses"]) and indexed and not missing and not invalid}


_RELEVANT_KEYS = ('"task_started"', '"task_complete"', '"turn_aborted"',
                  '"token_usage_record"', '"turn_context"', '"response_item"')
_TURNS_CACHE = OrderedDict()
_TURNS_CACHE_LIMIT = 64


def read_turns(path, thread, *, trial=False):
    # Usage reports re-read the same rollout logs on every call; reuse the parsed
    # turns while the file path, size and mtime stay unchanged. LRU eviction
    # keeps other threads warm on large boards.
    stamp = _log_stamp(path)
    cache_key = (str(path), stamp, thread, trial)
    if cache_key in _TURNS_CACHE:
        _TURNS_CACHE.move_to_end(cache_key)
        return _TURNS_CACHE[cache_key]
    frozen = _cache_lookup(path, stamp, thread, trial)
    if frozen is not None:
        turns = defaultdict(dict, _thaw(frozen))
        result = turns if trial else turns.values()
        if len(_TURNS_CACHE) >= _TURNS_CACHE_LIMIT:
            _TURNS_CACHE.popitem(last=False)
        _TURNS_CACHE[cache_key] = result
        return result
    turns = defaultdict(dict)
    seen = set()
    active = None
    with path.open(encoding="utf8") as log:
        for line in log:
            if not any(key in line for key in _RELEVANT_KEYS):
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                if not line.endswith("\n"):  # concurrently appended last record
                    break
                raise
            data = item.get("payload", {})
            tid = data.get("turn_id")
            if trial and item["type"]=="turn_context" and tid:
                active = tid
                turns[tid].setdefault("profiles",set()).add((data.get("model"),data.get("effort")))
            if trial and active and item["type"]=="response_item" and data.get("role")=="user":
                for block in data.get("content",[]):
                    markers=re.findall(r"\beval:([a-f0-9]{16}:[a-f0-9]{64})\b",block.get("text",""))
                    turns[active].setdefault("markers",set()).update(markers)
            if not tid:
                continue
            if item["type"] == "event_msg":
                kind = data.get("type")
                if kind == "task_started":
                    turns[tid]["start"] = data.get("started_at")
                elif kind in ("task_complete", "turn_aborted"):
                    if trial:
                        turns[tid]["outcome"] = kind
                    if data.get("started_at") is not None:
                        turns[tid]["start"] = data["started_at"]
                    stop = data.get("completed_at")
                    if stop is None and item.get("timestamp"):
                        stop = datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00")).timestamp()
                    turns[tid].update(stop=stop, ms=data.get("duration_ms"), ended=True)
            elif item["type"] == "token_usage_record" and data.get("thread_id") == thread:
                key = data.get("response_id")
                usage = data.get("usage") or {}
                counts = [usage.get(k) for k in ("input_tokens", "cached_input_tokens", "output_tokens")]
                if not key or not all(isinstance(n, int) and n >= 0 for n in counts) or counts[1] > counts[0]:
                    turns[tid]["invalid"] = True
                elif key not in seen:
                    seen.add(key)
                    totals = turns[tid].setdefault("tokens", [0, 0, 0])
                    for i, count in enumerate(counts):
                        totals[i] += count
    result = turns if trial else turns.values()
    if _log_stamp(path) != stamp:
        return result
    _cache_store(path, stamp, thread, trial, turns)
    if len(_TURNS_CACHE) >= _TURNS_CACHE_LIMIT:
        _TURNS_CACHE.popitem(last=False)
    _TURNS_CACHE[cache_key] = result
    return result


def measure(path):
    roles = {}
    durations = []
    # Capture one consistent board revision, then release it before touching the chat index or log files.
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=8)) as db:
        db.execute("BEGIN")
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        project = db.execute("SELECT lead,created FROM project").fetchone() if "project" in tables else None
        if not project:
            raise ValueError("no team board for this project")
        lead, since = project
        leadership = defaultdict(list)
        if "lead_handoffs" in tables:
            for previous, at in db.execute("SELECT previous,at FROM lead_handoffs ORDER BY id"):
                leadership[previous].append((since, at))
                since = at
        leadership[lead].append((since, None))
        owners = dict(db.execute("SELECT DISTINCT thread,role FROM chats JOIN tasks ON chats.slot=tasks.slot"))
        services = dict(db.execute("SELECT thread,slot FROM chats WHERE slot IN ('service/review','service/delivery')"))
        windows = list(db.execute("SELECT start,stop FROM windows"))
        for role, planned, accepted, returns in db.execute("""SELECT role,sum(hours),sum(state='accepted'),sum(returns)
                                                             FROM tasks GROUP BY role ORDER BY role"""):
            roles[role] = {"role": role, "planned_hours": planned, "accepted": accepted, "revisions": returns,
                           "measured": None}
        unbound = db.execute("""SELECT count(*) FROM tasks LEFT JOIN chats ON tasks.slot=chats.slot
                                WHERE state='reserved' AND thread IS NULL""").fetchone()[0]
        if "jobs" in tables:
            unbound += db.execute("""SELECT count(*) FROM jobs LEFT JOIN chats ON slot='service/'||kind
                                     WHERE state='offered' AND thread IS NULL""").fetchone()[0]
        if "card_timings" in tables:
            durations = [r[0] for r in db.execute("SELECT accepted-dispatched FROM card_timings WHERE accepted>=dispatched")]
    owners.update(services)
    # A handoff can happen during a pause. A coordinator owns only the overlap
    # of their tenure and an active project window.
    lead_windows = defaultdict(list)
    for thread, periods in leadership.items():
        for since, until in periods:
            for opened, closed in windows:
                start = max(since, opened)
                stop = min((t for t in (until, closed) if t is not None), default=None)
                if stop is None or start <= stop:
                    lead_windows[thread].append((start, stop))
    threads = set(owners) | set(lead_windows)
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").resolve()
    paths = rollout_paths(home, threads)
    accepted = sum(r["accepted"] for r in roles.values())
    returned = sum(r["revisions"] for r in roles.values())
    for role in services.values():
        roles[role] = {"role": role, "planned_hours": None, "accepted": None, "revisions": None, "measured": None}
    inp = cached = out = milliseconds = finished = partial = unrated = 0
    lead_tokens = worker_tokens = 0
    role_usage = defaultdict(lambda: [0, 0])
    missing = []
    for thread in sorted(threads):
        try:
            turns = read_turns(paths[thread], thread)
        except (OSError, ValueError, KeyError, TypeError):
            missing.append(thread)
            continue
        covered = False
        for turn in turns:
            start = turn.get("start")
            stop = turn.get("stop")
            if start is None:
                unrated += 1
                continue
            end = stop if stop is not None else time.time()
            # Codex timestamps use whole seconds. Count complete boundary turns.
            if not any(end >= int(a) and (b is None or start <= int(b)) for a, b in windows):
                continue
            was_lead = any(end >= int(a) and (b is None or start <= int(b))
                           for a, b in lead_windows.get(thread, ()))
            if not was_lead and thread not in owners:
                continue
            covered = True
            if not turn.get("ended"):
                partial += 1
                continue
            if "tokens" not in turn or turn.get("invalid") or stop is None or not isinstance(turn.get("ms"), int) or turn["ms"] < 0:
                unrated += 1
                continue
            i, c, o = turn["tokens"]
            inp += i
            cached += c
            out += o
            if was_lead:
                lead_tokens += i + o
            else:
                worker_tokens += i + o
                role_metrics = role_usage[owners[thread]]
                role_metrics[0] += i + o
                role_metrics[1] += turn["ms"]
            milliseconds += turn["ms"]
            finished += 1
        if not covered:
            missing.append(thread)
    total = inp + out
    complete = bool(finished and not (partial or unrated or missing or unbound))
    for role, (tokens, ms) in role_usage.items():
        roles[role]["measured"] = {"tokens": tokens, "agent_hours": round(ms / 3600000, 5),
                                   "tokens_per_agent_hour": round(tokens * 3600000 / ms) if ms else None}
    effective = (inp - cached) * PRICE_INPUT + cached * PRICE_CACHED + out * PRICE_OUTPUT
    return {"completed_measured_turns": finished, "unfinished_turns": partial, "unmeasured_turns": unrated,
            "dispatch_to_accept": {"measured_cards": len(durations), "median_seconds": round(median(durations), 2) if durations else None},
            "missing_chats": missing, "unbound_dispatches": unbound, "complete": complete,
            "accepted_tasks": accepted, "revisions": returned, "by_role": list(roles.values()),
            "measured": {"input": inp, "cached_input": cached, "uncached_input": inp - cached,
                         "cache_hit_ratio": round(cached / inp, 4) if inp else None,
                         "output": out, "total": total,
                         "effective_cost": round(effective),
                         "lead_tokens": lead_tokens, "worker_tokens": worker_tokens,
                         "agent_hours": round(milliseconds / 3600000, 5),
                         "tokens_per_agent_hour": round(total * 3600000 / milliseconds) if milliseconds else None}
            if finished else None,
            "project_tokens_per_accepted_task": round(total / accepted) if complete and accepted else None,
            "project_effective_cost_per_accepted_task": round(effective / accepted) if complete and accepted else None}
