"""Check the same local summarizer on different data formats and through MCP."""

import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


SCRIPTS = Path(__file__).resolve().parents[1] / "loomharness/scripts"


def shell(script, args=(), data=None):
    return subprocess.run(
        [sys.executable, "-I", "-X", "utf8", str(SCRIPTS / script), *map(str, args)],
        input=data,
        encoding="utf8",
        capture_output=True,
        timeout=20,
    )


def inspect(path, query, *options):
    result = shell("records.py", [path, "--query", json.dumps(query), *options])
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def rpc(**arguments):
    wire = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "harness_records", "arguments": arguments},
    }
    result = shell("mcp_server.py", data=json.dumps(wire) + "\n")
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["result"]


def verify(folder):
    invoices = folder / "invoices.csv"
    with invoices.open("w", encoding="utf-8-sig", newline="") as file:
        file.write(
            'team,amount,description\r\nops,1.25,"multi\r\nline"\r\nops,0.10,fee\r\nsales,4,other\r\n'
            "ops,bad,needs reconciliation\r\n"
        )
    query = {
        "where": {"team": "ops"},
        "group": ["team"],
        "sums": ["amount"],
        "fields": ["description"],
        "limit": 1,
    }
    page = inspect(invoices, query)
    assert page["matched"] == 3 and page["sums"] == {"amount": "1.35"} and page["missing_sums"] == {"amount": 1}
    assert page["groups"] == [{"values": ["ops"], "count": 3, "sums": {"amount": "1.35"}}]
    assert page["evidence"] == [{"at": {"line": 2}, "values": {"description": "multi\r\nline"}}]
    assert page["evidence_omitted"] == 2 and page["next_offset"] == 1
    second = inspect(invoices, dict(query, offset=1))
    assert second["evidence"] == [{"at": {"line": 4}, "values": {"description": "fee"}}]
    allowed = folder / "allowed"
    allowed.mkdir()
    result = rpc(path=str(allowed / ".." / invoices.name), root=str(folder), query=query)
    assert not result["isError"], result
    assert json.loads(result["content"][0]["text"]) == dict(page, source=str(invoices.resolve()))
    assert rpc(path=str(invoices), root=str(allowed))["isError"]

    sensor = folder / "sensors.ndjson"
    rows = [
        {"at": "2026-01-01T12:00:00+03:00", "temp": 1},
        {"at": "2026-01-01T09:05:00Z", "temp": 2},
        {"at": "2026-01-01T09:10:00Z", "temp": 3},
        {"at": "not known", "temp": 4},
    ]
    sensor.write_text("\n".join(map(json.dumps, rows)), encoding="utf8")
    page = inspect(
        sensor,
        {
            "gte": {"at": "2026-01-01T09:00:00Z"},
            "lt": {"at": "2026-01-01T09:10:00Z"},
            "sums": ["temp"],
            "limit": 0,
        },
    )
    assert page["matched"] == 2 and page["invalid_range"] == 1 and page["sums"] == {"temp": 3}
    assert page["evidence"] == [] and page["evidence_omitted"] == 2

    response = folder / "automation.log"
    response.write_text("startup\njob=import seen=20\njob=export seen=5\njob=import seen=12\n", encoding="utf8")
    pattern = r"job=(?P<job>\w+) seen=(?P<seen>\d+)"
    page = inspect(
        response, {"where": {"job": "import"}, "sums": ["seen"], "fields": ["seen"]}, "--pattern", pattern
    )
    assert page["unmatched_lines"] == 1 and page["sums"] == {"seen": 32}
    assert [r["at"] for r in page["evidence"]] == [{"line": 2}, {"line": 4}]

    events = folder / "events.json"
    records = [
        {"id": 1, "status": "ok", "details": "x" * 20000},
        {"id": 2, "status": "ok"},
        {"status": "pending"},
    ]
    events.write_text(json.dumps({"events": records}), encoding="utf8")
    page = inspect(events, {"where": {"id": 1}, "fields": ["id", "details"]}, "--rows-key", "events")
    assert page["matched"] == 1 and page["missing_filter"] == 1 and page["evidence_omitted"] == 1
    useful = inspect(events, {"where": {"id": 1}, "fields": ["id"]}, "--rows-key", "events")
    assert useful["evidence"] == [{"at": {"index": 0}, "values": {"id": 1}}]

    events.write_text(
        '[{"amount":0.100000000000000000000001},{"amount":0.2},{"amount":"0.2"}]', encoding="utf8"
    )
    page = inspect(
        events, {"gte": {"amount": 0.1}, "lt": {"amount": 0.3}, "group": ["amount"], "sums": ["amount"]}
    )
    assert page["sums"] == {"amount": "0.500000000000000000000001"}
    assert len(page["groups"]) == 3
    assert inspect(events, {"where": {"amount": 0.2}})["matched"] == 1

    builds = folder / "builds.tsv"
    builds.write_text("project\tduration\napi\t12\nweb\t8\napi\t13\n", encoding="utf8")
    spec = folder / "query.json"
    spec.write_text('{"group":["project"],"sums":["duration"],"group_limit":1}', encoding="utf8")
    page = json.loads(shell("records.py", [builds, "--spec", spec]).stdout)
    assert page["groups"] == [{"values": ["api"], "count": 2, "sums": {"duration": 25}}]
    assert page["groups_omitted"] == 1 and page["sums"] == {"duration": 33}
    builds.write_text("x\tx\n1\t2\n", encoding="utf8")
    assert shell("records.py", [builds]).returncode == 1


if __name__ == "__main__":
    with TemporaryDirectory() as directory:
        verify(Path(directory))
    print("OK: financial, sensor and build records; exact totals, source offsets, limits and MCP")
