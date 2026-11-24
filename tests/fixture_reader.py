"""Read-only MCP tool for paired benchmarks on hosts with different shells."""

import json
from pathlib import Path
import sys


folder = Path(sys.argv[1])
allowed = {"A.log", "B.log", "C.log", "D.log", "E.log", "pricing.py", *(f"quote_{i}.py" for i in range(6))}
tool = {"name": "read_fixture", "description": "Open a synthetic benchmark file. Use find for literal matches.",
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
        "inputSchema": {"type": "object", "properties": {"file": {"type": "string"},
                        "find": {"type": "string"}, "start": {"type": "integer", "minimum": 1},
                        "lines": {"type": "integer", "minimum": 1}}, "required": ["file"], "additionalProperties": False}}


for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    method = msg["method"]
    reply = {"jsonrpc": "2.0", "id": msg["id"]}
    if method == "initialize":
        reply["result"] = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                           "serverInfo": {"name": "fixture-reader", "version": "1"}}
    elif method == "tools/list":
        reply["result"] = {"tools": [tool]}
    elif method == "tools/call":
        params = msg["params"]
        args = params["arguments"]
        name = args["file"]
        permitted = (params["name"] == "read_fixture" and name in allowed and
                     (folder / name).is_file() and not (folder / name).is_symlink())
        content = "Unknown fixture."
        if permitted:
            lines = (folder / name).read_text(encoding="utf8").splitlines()
            start = max(1, args.get("start", 1))
            selected = lines[start - 1:start - 1 + args.get("lines", len(lines))]
            content = "\n".join(f"{i}: {s}" for i, s in enumerate(selected, start) if args.get("find", "") in s)
        reply["result"] = {"content": [{"type": "text", "text": content}], "isError": not permitted}
    elif method == "ping":
        reply["result"] = {}
    else:
        reply["error"] = {"code": -32601, "message": "Unsupported method"}
    print(json.dumps(reply), flush=True)
