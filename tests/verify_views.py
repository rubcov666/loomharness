"""Exercise the installed CLI contract without a model or extra packages."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(os.environ.get("LOOMHARNESS_REPO", Path(__file__).resolve().parents[1])) / "loomharness/scripts/view.py"


class Views(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.file = self.root / "report.json"

    def view(self, *flags, success=True):
        original = self.file.read_bytes()
        p = subprocess.run(
            [sys.executable, "-I", "-X", "utf8", str(SCRIPT), str(self.file), *flags],
            capture_output=True,
        )
        self.assertEqual(p.returncode == 0, success, p.stderr)
        self.assertEqual(self.file.read_bytes(), original)
        if not success:
            self.assertFalse(p.stdout)
            self.assertTrue(p.stderr.strip())
        return p.stdout

    def store(self, rows):
        self.file.write_text(
            json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf8"
        )

    def test_file_identity_and_text_context_without_git(self):
        book = self.root / "Бюджет.xlsx"
        book.write_bytes(b"PK\x03\x04\x00\xff")
        def invoke(file, *args):
            return subprocess.run([sys.executable, "-I", "-X", "utf8", SCRIPT, file, *args],
                                  cwd=self.root, capture_output=True, encoding="utf8")
        fingerprint = hashlib.sha256(book.read_bytes()).hexdigest()
        self.assertIn(fingerprint, invoke(book, "--fingerprint").stdout)
        match = invoke(book, "--expect-sha256", fingerprint)
        self.assertEqual(match.returncode, 0)
        self.assertIn("UNCHANGED", match.stdout)
        book.write_bytes(b"PK\x03\x04\x00\xfe")
        changed = invoke(book, "--expect-sha256", fingerprint)
        self.assertEqual(changed.returncode, 1)
        self.assertIn("CHANGED", changed.stdout)
        self.assertEqual(invoke(book, "--expect-sha256", "bad").returncode, 2)
        self.assertEqual(invoke(book, "--find", "PK").returncode, 2)
        notes = self.root / "minutes.txt"
        notes.write_text("Minutes\nСрок: пятница\nИсключение: выходной\nПодпись\n\nСрок: уточняется\n",
                         encoding="utf-16")
        first = invoke(notes, "--find", "Срок", "--limit", "1")
        self.assertEqual(first.returncode, 0)
        self.assertIn("3 | Исключение: выходной", first.stdout)
        self.assertIn("--start 6", first.stdout)
        self.assertIn("6 | Срок: уточняется", invoke(notes, "--find", "Срок", "--start", "6").stdout)
        self.assertEqual(invoke(notes, "--find", "Отменено").returncode, 1)

    def test_repeat_view_receipt_tracks_source_and_projection(self):
        self.store([{"id": n, "message": f"line {n}"} for n in range(150)])
        header, content = self.view("--fields", "id", "--receipt", "new").split(b"\n", 1)
        receipt = header.decode().removeprefix("receipt=")
        self.assertRegex(receipt, r"^[0-9a-f]{64}$")
        self.assertEqual(content, self.view("--fields", "id"))
        self.assertEqual(self.view("--fields", "id", "--receipt", receipt), b"UNCHANGED\n")
        self.assertNotEqual(self.view("--fields", "message", "--receipt", receipt).splitlines()[0], header)
        self.view("--receipt", "broken", success=False)
        self.file = self.file.rename(self.root / "renamed.json")
        self.assertNotEqual(self.view("--fields", "id", "--receipt", receipt).splitlines()[0], header)

        self.file = self.root / "build.log"
        self.file.write_bytes(b"same message\n" * 5000 + b"error 1\n")
        header, cropped = self.view("--receipt", "new").split(b"\n", 1)
        self.file.write_bytes(b"same message\n" * 5000 + b"error 2\n")
        changed, display = self.view("--receipt", header.decode()[8:]).split(b"\n", 1)
        self.assertEqual(display, cropped)
        self.assertNotEqual(changed, header)
        call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "harness_view", "arguments": {"path": str(self.file), "receipt": changed.decode()[8:]}}}
        result = subprocess.run([sys.executable, "-I", str(SCRIPT.with_name("mcp_server.py"))],
                                input=json.dumps(call) + "\n", capture_output=True, encoding="utf8", check=True)
        self.assertEqual(json.loads(result.stdout)["result"]["content"][0]["text"], "UNCHANGED\n")

    def test_count_matching_lines_via_cli_and_mcp(self):
        self.file = self.root / "запуск.log"
        self.file.write_text("Сбой\nтихо\nСбой Сбой\nсбой\nСбой\nхвост\n", encoding="utf-32")
        expected = "Совпадающих строк с 2: 2.\n"
        flags = ("--find", "Сбой", "--start", "2", "--limit", "1")
        self.assertEqual(self.view(*flags, "--count").decode(), expected)
        page = self.view(*flags).decode()
        self.assertTrue(page.startswith(expected))
        self.assertIn("5 | Сбой\n", page)
        self.assertIn("--start 5", page)
        args = {"path": str(self.file), "text": "Сбой", "start": 2, "limit": 1, "count_only": True}
        def find(**overrides):
            call = {"id": 1, "method": "tools/call", "params": {
                "name": "harness_find", "arguments": {**args, **overrides}}}
            rpc = subprocess.run([sys.executable, "-I", "-X", "utf8", SCRIPT.with_name("mcp_server.py")],
                                 input=json.dumps(call) + "\n", capture_output=True, encoding="utf8", check=True)
            return json.loads(rpc.stdout)["result"]
        self.assertEqual(find(), {"content": [{"type": "text", "text": expected}], "isError": False})
        self.assertEqual(find(count_only=False)["content"][0]["text"], page)
        self.assertTrue(find(start=0)["isError"])
        self.assertTrue(find(count_only="false")["isError"])
        empty = subprocess.run([sys.executable, "-I", "-X", "utf8", SCRIPT, self.file,
                                "--find", "Сбой", "--start", "6", "--count"], capture_output=True)
        self.assertEqual((empty.returncode, empty.stdout.decode()), (1, "Совпадающих строк с 6: 0.\n"))
        self.view("--count", success=False)
        self.file.write_bytes(b"match\n" * 5000 + b"\0")
        self.view("--find", "match", "--count", success=False)
        self.view("--find", "match", "--limit", "1", success=False)

    def test_numbered_source_pages_via_cli_and_mcp(self):
        self.file = self.root / "original.json"
        self.file.write_bytes(("first\r\n" + "я" * 70000 + "\r\nlast\r\n").encode("utf-16"))
        page = self.view("--start", "2", "--lines", "1").decode()
        self.assertEqual(page, "2 | " + "я" * 440 + " […]\nПродолжение: --start 3.\n")
        self.assertEqual(self.view("--start", "3").decode(), "3 | last\n")
        self.assertIn("данных нет", self.view("--start", "4").decode())
        for flags in (("--lines", "0"), ("--lines", "101"), ("--start", "0"),
                      ("--lines", "2", "--fields", "id"), ("--start", "2", "--receipt", "new")):
            self.view(*flags, success=False)
        call = {"id": 1, "method": "tools/call", "params": {"name": "harness_view",
                "arguments": {"path": str(self.file), "root": str(self.root), "start": 2, "lines": 1}}}
        rpc = subprocess.run([sys.executable, "-I", "-X", "utf8", SCRIPT.with_name("mcp_server.py")],
                             input=json.dumps(call) + "\n", capture_output=True, encoding="utf8", check=True)
        result = json.loads(rpc.stdout)["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["content"][0]["text"], page)

    def test_source_pages_observe_utf8_byte_budget_and_read_past_render_limit(self):
        self.file.write_text(("📖" * 440 + "\n") * 100, encoding="utf-32")
        output = self.view("--lines", "100")
        self.assertLess(len(output), 48 * 1024 + 100)
        rows = output.decode().splitlines()
        self.assertEqual(rows[-1], f"Продолжение: --start {len(rows)}.")
        self.assertTrue(self.view("--start", str(len(rows)), "--lines", "1").decode().startswith(
            f"{len(rows)} | 📖"))
        self.file.write_bytes(b"\x00binary\n")
        self.view("--lines", "2", success=False)
        with self.file.open("wb") as stream:
            stream.write(b"first\nsecond\n")
            stream.truncate(257 * 1024 * 1024)
        proc = subprocess.run([sys.executable, "-I", "-X", "utf8", SCRIPT, self.file, "--lines", "1"],
                              capture_output=True, check=True)
        self.assertEqual(proc.stdout.decode(), "1 | first\nПродолжение: --start 2.\n")

    def test_projection_keeps_failures_and_unknown_fields(self):
        rows = [
            dict(
                id=i,
                checks="ok",
                headSha=hashlib.sha1(str(i).encode()).hexdigest(),
                createdAt="2026-02-01T02:04:00Z",
                url=f"https://example.invalid/runs/{i}",
                note={"detail": None},
                title="regular job",
            )
            for i in range(60)
        ]
        rows[-1].update(
            checks="failed",
            note={"detail": {"new_schema": ["unexpected result", False]}},
            title="payment mismatch",
        )
        self.store(rows)
        header, body = self.view("--fields", "id", "checks").decode().split("\n", 1)
        self.assertIn("All 60 rows", header)
        self.assertIn("createdAt, headSha, url", header)
        self.assertEqual(
            json.loads(body),
            [{k: v for k, v in r.items() if k not in {"createdAt", "headSha", "url"}} for r in rows],
        )
        self.assertEqual(json.loads(self.view()), rows)
        rows[-1].pop("checks")
        self.store(rows)
        self.assertEqual(json.loads(self.view("--fields", "id", "checks")), rows)

    def test_requested_metadata_and_unusual_values(self):
        rows = [
            dict(
                id=i,
                url=f"https://example.invalid/jobs/{i}",
                headSha="a" * 40,
                createdAt="2026-01-01T00:00:00Z",
                details={},
            )
            for i in range(50)
        ]
        rows[-1]["createdAt"] = {"error": "invalid timestamp"}
        self.store(rows)
        h, body = self.view("--fields", "id", "url").decode().split("\n", 1)
        self.assertEqual(h, "All 50 rows, original order. Omitted metadata: headSha.")
        self.assertEqual(
            json.loads(body), [{k: v for k, v in r.items() if k != "headSha"} for r in rows]
        )
        rows[-1]["url"] = "https://example.invalid/errors/501"
        rows[-1]["createdAt"] = "2026-02-31T24:00:00Z"
        self.store(rows)
        out = self.view("--fields", "id")
        self.assertIn(rows[-1]["url"].encode(), out)
        self.assertIn(rows[-1]["createdAt"].encode(), out)

    def test_table_keeps_typed_scalars_and_escaping(self):
        values = [
            "true",
            True,
            "false",
            False,
            None,
            "null",
            "0",
            "01",
            "",
            7,
            "a,b",
            'a"b',
            "two\nlines",
            "проверка",
            "x\\y",
            "  spaced  ",
        ]
        rows = [{"row_identifier": i, "observed_value": v} for i, v in enumerate(values * 3)]
        self.store(rows)
        lines = self.view().decode().splitlines()
        self.assertEqual(lines[0], f"[{len(rows)}]{{row_identifier,observed_value}}:")
        for line, row in zip(lines[1:], rows, strict=True):
            key, cell = line.strip().split(",", 1)
            self.assertEqual(int(key), row["row_identifier"])
            self.assertEqual(json.dumps(json.loads(cell)), json.dumps(row["observed_value"]))

    def test_nested_or_unsafe_numbers_stay_lossless(self):
        for value in ["1.000000000000000000321", "-0", "1e400", "9007199254740993"]:
            raw = (
                "[   " + ",  ".join(['{"value": ' + value + ', "literal":" a  b " }'] * 40) + " ]"
            ).encode()
            self.file.write_bytes(raw)
            report = self.view("--fields", "value")
            self.assertEqual(report.count(value.encode()), 40)
            self.assertEqual(report.count(b'" a  b "'), 40)
            self.assertNotIn(b"Omitted", report)
        self.store([{"outer": [{"error": True}, {"error": "true"}]}] * 40)
        self.assertEqual(self.view(), self.file.read_bytes())
        self.store([{"id": 1}])
        self.assertEqual(self.view(), b'[{"id":1}]')

    def test_damaged_input_fails_with_no_partial_data(self):
        for raw in [
            b'[{"id":1, "id":2}]',
            b'[{"outer":{"x":0,"x":1}}]',
            b"[NaN]",
            b"[1,",
            b'{"v": "\xff"}',
            b'{"v": 1}\x00',
        ]:
            self.file.write_bytes(raw)
            self.view(success=False)

    def test_git_changes_and_fallback(self):
        def git(*args):
            return subprocess.run(
                [
                    "git",
                    "-c",
                    "core.autocrlf=false",
                    "-c",
                    "user.name=Fixture",
                    "-c",
                    "user.email=fixture@example.invalid",
                    "-c",
                    "commit.gpgSign=false",
                    *args,
                ],
                cwd=self.root,
                capture_output=True,
                check=True,
            ).stdout

        git("init", "-q")
        sub = self.root / "sub dir"
        sub.mkdir()
        self.file = sub / "проверка.txt"
        baseline = [f"rule-{i:03} validated\r\n".encode() for i in range(170)]
        self.file.write_bytes(b"".join(baseline))
        git("add", "--", ".")
        git("commit", "-qm", "base")
        commit = git("rev-parse", "HEAD").decode().strip()
        self.assertIn(b"Unchanged, byte-for-byte", self.view("--base", commit))
        updated = baseline[:]
        updated[95] = b"rule-095 ERROR wrong total\r\n"
        updated.pop(115)
        updated[-1] = b"tail without newline"
        self.file.write_bytes(b"".join(updated))
        report = self.view("--base", commit)
        self.assertEqual(self.view("--base", commit, "--fields", "scope", "mode"), report)
        self.assertIn(commit.encode(), report)
        self.assertIn(b"+rule-095 ERROR wrong total\r\n", report)
        self.assertIn(b"-rule-115 validated\r\n", report)
        self.assertIn(b"No newline at end of file", report)
        # Reconstruct the exact bytes from the selected patch on a clean copy.
        patch = report.split(b"\n", 1)[1].replace(b"a/before", b"a/copy").replace(b"b/after", b"b/copy")
        (self.root / "copy").write_bytes(b"".join(baseline))
        subprocess.run(
            ["git", "-c", "core.autocrlf=false", "apply", "--whitespace=nowarn", "-"],
            cwd=self.root,
            input=patch,
            check=True,
        )
        self.assertEqual((self.root / "copy").read_bytes(), self.file.read_bytes())
        self.file.write_bytes(b"".join(reversed(updated)))
        self.assertEqual(self.view("--base", commit), self.file.read_bytes())
        self.assertEqual(self.view("--base", "0" * 40), self.file.read_bytes())
        self.view("--base", "HEAD", success=False)
        self.file = self.root / "new.txt"
        self.file.write_bytes(b"new error")
        self.assertEqual(self.view("--base", commit), b"new error")


if __name__ == "__main__":
    unittest.main(verbosity=2)
