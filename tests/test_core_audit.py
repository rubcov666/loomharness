"""Unit tests for the portable core of loomharness.

Run from the skill root:  python -m unittest discover -s tests -v
No network, no Codex Desktop, no git transport: only local files and sqlite.
"""

from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch


SKILL_ROOT = Path(__file__).resolve().parents[1] / "loomharness"
SCRIPTS = SKILL_ROOT / "scripts"
TMP_BASE = Path(__file__).resolve().parents[1] / "tests" / ".tmp"
sys.path.insert(0, str(SCRIPTS))


def make_tmp():
    # The system temp directory can be inaccessible in sandboxed runs.
    # Create test directories with pathlib under the writable tests folder
    # on platforms where tempfile.mkdtemp is intercepted.
    TMP_BASE.mkdir(parents=True, exist_ok=True)
    target = TMP_BASE / ("tmp-" + next(tempfile._get_candidate_names()))
    target.mkdir()
    return target

import hk  # noqa: E402

metrics = hk.load("metrics")
plan = hk.load("plan")
view = hk.load("view")
harness = hk.load("harness")
inbox = hk.load("inbox")
usage = hk.load("team_usage")
self_update = hk.load("self_update")


class SeriesTest(unittest.TestCase):
    def test_summarize_counts_and_gaps(self):
        data = {
            "windows": [
                {"start": "2026-01-01T09:00:00Z", "end": "2026-01-01T09:01:00Z", "expected": 1},
                {"start": "2026-01-01T09:01:00Z", "end": "2026-01-01T09:02:00Z"},
            ],
            "samples": [
                {"at": "2026-01-01T09:00:30Z", "value": 2000},
                {"at": "2026-01-01T09:05:00Z", "value": 5000},
            ],
        }
        result = metrics["summarize"](data, 2000)
        self.assertEqual(result["coverage"][0]["count"], 1)
        self.assertEqual(result["gaps"], ["2026-01-01T09:01:00Z"])
        # Peak and alerts use observations inside the windows.
        self.assertEqual(result["peak"]["value"], 2000)
        self.assertEqual(len(result["alerts"]), 1)
        self.assertEqual(len(result["outside_alerts"]), 1)

    def test_summarize_rejects_overlap(self):
        data = {"windows": [
            {"start": "2026-01-01T09:00:00Z", "end": "2026-01-01T09:02:00Z"},
            {"start": "2026-01-01T09:01:00Z", "end": "2026-01-01T09:03:00Z"}],
            "samples": []}
        with self.assertRaises(ValueError):
            metrics["summarize"](data, 1)


class PlanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(make_tmp())
        self.root = self.tmp / "proj"
        self.root.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        plan["_EXCERPT_CACHE"].clear()

    def test_for_prompt_budget_and_cache(self):
        (self.root / "HANDOFF.md").write_text(
            "# Plan\n## Current\n- [x] core done\n- [ ] next tests\n", encoding="utf8")
        first = plan["for_prompt"](self.root, "what is next")
        self.assertIn("HANDOFF.md", first)
        self.assertLessEqual(len(first), 2000 + 400)
        again = plan["for_prompt"](self.root, "what is next")
        self.assertIs(first, again)  # File signatures determine cache identity.
        (self.root / "HANDOFF.md").write_text("# Plan\n- [ ] changed\n", encoding="utf8")
        changed = plan["for_prompt"](self.root, "what is next")
        self.assertIsNot(first, changed)

    def test_for_prompt_ignores_long_prompts(self):
        self.assertEqual(plan["for_prompt"](self.root, "what is next " + "x" * 200), "")

    def test_counted_rows(self):
        (self.root / "tasks.csv").write_text("id,status\n1,done\n2,done\n3,open\n", encoding="utf8")
        summary = plan["counted_rows"](self.root, {"file": "tasks.csv", "column": "status",
                                                "values": ["done", "open"]})
        self.assertIn("done = 2", summary)
        self.assertIn("other = 0", summary)


class ViewTest(unittest.TestCase):
    def test_choose_prefers_smaller_representation(self):
        rows = [{"identifier": i, "display_name": f"name-{i}", "status": "ready"} for i in range(12)]
        full = json.dumps(rows, ensure_ascii=False)
        table = "identifier display_name status\n" + "\n".join(
            f'{r["identifier"]} {r["display_name"]} {r["status"]}' for r in rows)
        self.assertEqual(view["choose"](full, [table]), table)
        # Keep the original when sizes are close.
        self.assertEqual(view["choose"]('{"a": 1}', ['a 1']), '{"a": 1}')

    def test_render_caps_by_whole_lines(self):
        tmp = Path(make_tmp())
        try:
            big = tmp / "big.log"
            big.write_text("\n".join(f"line {i:05d} " + "x" * 200 for i in range(20000)), encoding="utf8")
            out = view["render"](big)
            self.assertLessEqual(len(out.encode("utf8")), view["RENDER_CAP"] + 400)
            self.assertIn("REVIEW", out)
            self.assertNotIn("\x00", out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class HarnessTest(unittest.TestCase):
    def test_workspace_binding_preserves_existing_board_and_update_guard(self):
        root = self.tmp / "project"
        root.mkdir()
        entry = harness["profile_board"](root)
        with patch.dict(os.environ, LOOMHARNESS_HOME=str(root / ".loomharness/data")):
            self.assertEqual(harness["team_board"](root), entry.resolve())
            try:
                storage = harness["board_storage"](root, True)
            except OSError as error:
                if os.name == "nt" and getattr(error, "winerror", None) == 1314:
                    self.skipTest("workspace binding needs permission to create a symbolic link")
                raise
            self.assertEqual(storage["location"], "workspace")
            self.assertFalse(storage["exists"])
            local = Path(storage["path"])
            with closing(sqlite3.connect(local)) as db, db:
                db.execute("CREATE TABLE tasks(state)")
                db.execute("INSERT INTO tasks VALUES('working')")
            self.assertTrue(self_update["any_active_work"]())
        self.assertTrue(entry.samefile(local))
        self.assertEqual(harness["team_board"](root), local)
        self.assertEqual(harness["board_storage"](root, True)["path"], str(local))
        other = self.tmp / "legacy"
        other.mkdir()
        original = harness["profile_board"](other)
        original.write_bytes(b"existing")
        self.assertEqual(harness["board_storage"](other, True)["location"], "profile")
        self.assertEqual(original.read_bytes(), b"existing")
        candidate = other / ".loomharness/data/teams" / original.name
        candidate.parent.mkdir(parents=True)
        candidate.write_bytes(b"independent")
        with self.assertRaisesRegex(ValueError, "two existing boards"):
            harness["board_storage"](other, True)
        self.assertEqual(candidate.read_bytes(), b"independent")

    def setUp(self):
        self.tmp = Path(make_tmp())
        os.environ["CODEX_HOME"] = str(self.tmp / "home")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("CODEX_HOME", None)

    def test_cached_digest_detects_change(self):
        path = self.tmp / "artifact.bin"
        path.write_bytes(b"one")
        original = path.stat()
        first = harness["cached_digest"](path)
        self.assertEqual(first, harness["cached_digest"](path))
        path.write_bytes(b"two")
        os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
        second = harness["cached_digest"](path)
        self.assertNotEqual(first, second)

    def test_cheap_hook_result(self):
        marker = object()
        self.assertIs(harness["cheap_hook_result"]({"hook_event_name": "SessionStart"}), harness["_NEEDS_DB"])
        self.assertIs(harness["cheap_hook_result"](
            {"hook_event_name": "PreToolUse", "tool_name": "apply_patch",
             "cwd": str(self.tmp)}), harness["_NEEDS_DB"])
        event = {"hook_event_name": "PreToolUse", "tool_name": "shell", "cwd": str(self.tmp)}
        self.assertIsNone(harness["cheap_hook_result"](event))
        (self.tmp / "AGENT_STOP").write_text("stop", encoding="utf8")
        self.assertIs(harness["cheap_hook_result"](event), harness["_NEEDS_DB"])


class InboxTest(unittest.TestCase):
    def test_changed_parts_cursors(self):
        scope = ["board", "card"]
        first = inbox["changed_parts"](scope, {"a": 1}, {"b": 2}, None)
        self.assertTrue(first["changed"])
        cursor = first["cursor"]
        same = inbox["changed_parts"](scope, {"a": 1}, {"b": 2}, cursor)
        self.assertIsNone(same)
        other = inbox["changed_parts"](scope, {"a": 2}, {"b": 2}, cursor)
        self.assertTrue(other["changed"])
        self.assertNotIn("card", other)


class UsageTest(unittest.TestCase):
    def test_persistent_turn_cache_reuses_only_current_log_and_mode(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, CODEX_HOME=tmp):
            log = Path(tmp) / "rollout.jsonl"
            row = {"type": "turn_context", "payload": {"turn_id": "t", "model": "m", "effort": "low"}}
            log.write_text(json.dumps(row) + "\n", encoding="utf8")
            original = log.stat()
            trial = usage["read_turns"](log, "chat", trial=True)
            self.assertEqual(trial["t"]["profiles"], {("m", "low")})
            usage["_TURNS_CACHE"].clear()
            with patch.object(Path, "open", side_effect=AssertionError("reopened unchanged rollout")):
                self.assertEqual(usage["read_turns"](log, "chat", trial=True), trial)
            self.assertEqual(list(usage["read_turns"](log, "chat")), [])
            row["payload"]["model"] = "n"
            replacement = log.with_suffix(".new")
            replacement.write_text(json.dumps(row) + "\n", encoding="utf8")
            os.utime(replacement, ns=(original.st_atime_ns, original.st_mtime_ns))
            replacement.replace(log)
            self.assertEqual(usage["read_turns"](log, "chat", trial=True)["t"]["profiles"], {("n", "low")})
            with closing(sqlite3.connect(usage["_cache_db"]())) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM turn_cache").fetchone()[0], 2)

    def test_freeze_thaw_roundtrip(self):
        turns = {"t1": {"tokens": [1, 2, 3], "profiles": {("m1", "low"), ("m2", "low")},
                        "markers": {"eval:" + "a" * 16 + ":" + "b" * 64}}}
        thawed = usage["_thaw"](usage["_freeze"](turns))
        self.assertEqual(thawed["t1"]["profiles"], turns["t1"]["profiles"])
        self.assertEqual(thawed["t1"]["markers"], turns["t1"]["markers"])
        self.assertEqual(thawed["t1"]["tokens"], [1, 2, 3])

    def test_rollout_paths_index(self):
        tmp = Path(make_tmp())
        try:
            home = tmp / "home"
            home.mkdir()
            os.environ["CODEX_HOME"] = str(home)
            state = home / "state_1.sqlite"
            with closing(sqlite3.connect(state)) as db, db:
                db.execute("CREATE TABLE threads (id TEXT, rollout_path TEXT)")
                log = tmp / "rollout.jsonl"
                log.write_text('{"type":"event_msg"}\n', encoding="utf8")
                db.execute("INSERT INTO threads VALUES(?,?)", ("thread-1", str(log)))
            found = usage["rollout_paths"](home, {"thread-1", "missing"})
            self.assertEqual(found["thread-1"], log)
            self.assertNotIn("missing", found)
            latest = home / "state_2.sqlite"
            with closing(sqlite3.connect(latest)) as live, live:
                live.execute("PRAGMA journal_mode=WAL")
                live.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT)")
                self.assertEqual(usage["rollout_paths"](home, {"thread-1"}), {"thread-1": log})
                newer = tmp / "new.jsonl"
                newer.touch()
                live.execute("INSERT INTO threads VALUES(?,?)", ("thread-1", str(newer)))
                live.commit()  # Leave the connection open and the new row in the WAL.
                self.assertEqual(usage["rollout_paths"](home, {"thread-1", "missing"}), {"thread-1": newer})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            os.environ.pop("CODEX_HOME", None)


class SelfUpdateTest(unittest.TestCase):
    def test_overlay_backup_rollback(self):
        tmp = Path(make_tmp())
        try:
            os.environ["CODEX_HOME"] = str(tmp / "home")
            target = tmp / "install"
            (target / "scripts").mkdir(parents=True)
            (target / "VERSION").write_text("1.0.0\n", encoding="utf8")
            (target / "scripts" / "keep.py").write_text("# keep\n", encoding="utf8")
            source = tmp / "new"
            (source / "scripts").mkdir(parents=True)
            (source / "VERSION").write_text("2.0.0\n", encoding="utf8")
            (source / "scripts" / "keep.py").write_text("# keep\n", encoding="utf8")
            (source / "added.md").write_text("new\n", encoding="utf8")
            backup = self_update["backup_current"](target)
            self_update["overlay"](source, target)
            self.assertEqual((target / "VERSION").read_text().strip(), "2.0.0")
            self.assertTrue((target / "added.md").is_file())
            self_update["overlay"](Path(backup), target)
            self.assertEqual((target / "VERSION").read_text().strip(), "1.0.0")
            # Untracked top-level names survive an update/rollback (user files
            # next to the skill); tracked top-level names are synced fully.
            self.assertTrue((target / "added.md").is_file())
            self.assertTrue((target / "scripts" / "keep.py").is_file())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            os.environ.pop("CODEX_HOME", None)


class LoaderTest(unittest.TestCase):
    def test_load_returns_runpy_style_namespace(self):
        ns = hk.load("metrics")
        self.assertTrue(callable(ns["summarize"]))
        self.assertIs(ns, hk.load("metrics"))


if __name__ == "__main__":
    unittest.main()

