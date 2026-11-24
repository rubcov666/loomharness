"""Run two complete non-build workflows with the same public harness commands."""

import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
from xml.etree import ElementTree


SCRIPTS = Path(os.environ.get("LOOMHARNESS_REPO", Path(__file__).resolve().parents[1])) / "loomharness/scripts"


def invoke(root, tool, *args, expected=0):
    result = subprocess.run([sys.executable, "-I", "-X", "utf8", SCRIPTS / tool, *args],
                            cwd=root, capture_output=True, encoding="utf8", timeout=20)
    assert result.returncode == expected, result
    return result.stdout


def report_export(root):
    (root / "orders.json").write_text('[{"state":"open"},{"state":"done"},{"state":"done"}]')
    (root / "layout.txt").write_text("approved-count")
    script = root / "export.cjs"
    script.write_text("""const fs = require('node:fs');
const rows = JSON.parse(fs.readFileSync('orders.json', 'utf8'));
const label = fs.readFileSync('layout.txt', 'utf8');
const count = rows.filter(row => row.state === 'done').length;
fs.writeFileSync('report.xml', `<report><${label}>${count}</${label}></report>`);
""", encoding="utf8")
    node = shutil.which("node")
    invoke(root, "context.py", "run", "--log", "export.log", "--", node, script)
    assert ElementTree.parse(root / "report.xml").findtext("approved-count") == "2"
    version = subprocess.check_output([node, "--version"], text=True).strip()
    opts = ("reuse", "--record", "verified.record", "--method", "node " + version,
            "--input", "orders.json", "--input", "layout.txt", "--input", "export.cjs", "--result", "report.xml")
    invoke(root, "context.py", *opts, "--save")
    assert invoke(root, "context.py", *opts).strip() == "UNCHANGED"
    (root / "orders.json").write_text('[{"state":"done"}]')
    assert "RECHECK" in invoke(root, "context.py", *opts, expected=1)


def lost_acknowledgement(root):
    records = []

    class Cards(BaseHTTPRequestHandler):
        def do_POST(self):
            assert self.path == "/cards"
            records.append("card-17")
            self.close_connection = True  # The write happened before the connection closed.

        def do_GET(self):
            assert self.path == "/cards/card-17"
            self.send_response(200 if "card-17" in records else 404)
            self.end_headers()
            self.wfile.write(b'{"id":"card-17","state":"saved"}')

        def log_message(self, *args):
            pass

    client = root / "submit.py"
    client.write_text("""import http.client, sys
conn = http.client.HTTPConnection("127.0.0.1", int(sys.argv[1]), timeout=3)
conn.request("POST", "/cards")
try:
    conn.getresponse()
except http.client.RemoteDisconnected:
    print("card-17: incomplete response")
    sys.exit(7)
""", encoding="utf8")
    with ThreadingHTTPServer(("127.0.0.1", 0), Cards) as service:
        thread = threading.Thread(target=service.serve_forever)
        thread.start()
        try:
            send = ("run", "--log", "card.log", "--", sys.executable, client, str(service.server_port))
            summary = invoke(root, "context.py", *send, expected=7)
            assert "exit=7" in summary and "тестов=" not in summary
            assert "incomplete response" in invoke(root, "view.py", "card.log", "--find", "card-17")
            conn = http.client.HTTPConnection("127.0.0.1", service.server_port, timeout=3)
            try:
                conn.request("GET", "/cards/card-17")
                response = conn.getresponse()
                assert response.status == 200 and json.load(response)["state"] == "saved"
            finally:
                conn.close()
            invoke(root, "context.py", *send, expected=1)
            assert records == ["card-17"]
        finally:
            service.shutdown()
            thread.join()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as temp:
        report_export(Path(temp))
        lost_acknowledgement(Path(temp))
    print("PASS: verified report reuse and recovery from a lost service acknowledgement")
