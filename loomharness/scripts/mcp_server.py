"""MCP server exposing the portable loomharness core to any MCP-capable agent.

Read-only local tools: compact file views, exact-text search, lexical code map
and telemetry windows. Stdio JSON-RPC, standard library only. Add to an agent
as a stdio MCP server pointing at this file with the same Python (3.11+).
"""

import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

import context
import history
import metrics
import view

NAME = "loomharness"
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")


def package_version():
    try:
        return (Path(__file__).resolve().parent.parent / "VERSION").read_text(encoding="utf8").strip()
    except OSError:
        return "unknown"


VERSION = package_version()

TOOLS = [
    {
        "name": "harness_context_budget",
        "description": "Decide when to compact the active model context and how much to keep. "
                       "Use current input tokens, including cached input once, and the active model window.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "window_tokens": {"type": "integer", "minimum": 4},
                "used_tokens": {"type": "integer", "minimum": 0},
                "protected_tokens": {"type": "integer", "minimum": 0},
                "trigger_cap": {"type": "integer", "minimum": 2},
                "target_tokens": {"type": "integer", "minimum": 1},
                "remaining_calls": {"type": "integer", "minimum": 0, "description": "Expected ordinary calls including the next."},
                "compaction_tokens": {"type": "integer", "minimum": 0, "description": "Input plus output to generate one summary."},
                "reread_tokens": {"type": "integer", "minimum": 0, "description": "Total extra tokens to recover omitted details."},
                "client": {"type": "string", "description": "Optional CLI name for native settings."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "harness_view",
        "description": "Read a compact file or a numbered source page. Use start/lines for a known location, "
                       "or fields/base/receipt for an automatic view.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to a text file."},
                "root": {"type": "string", "description": "Optional allowed root; the path must stay inside it."},
                "start": {"type": "integer", "minimum": 1, "description": "First source line."},
                "lines": {"type": "integer", "minimum": 1, "maximum": 100, "description": "Source lines per page (default 20)."},
                "fields": {"type": "array", "items": {"type": "string"},
                           "description": "JSON fields needed for the question."},
                "base": {"type": "string", "description": "Verified commit hash for a git diff."},
                "receipt": {"type": "string", "description": "Use new on first read or a previous view receipt to skip a repeat."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "harness_find",
        "description": "Case-sensitive literal search. Return a count or numbered excerpts with context and a continuation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "root": {"type": "string", "description": "Optional allowed root; the path must stay inside it."},
                "text": {"type": "string", "description": "Single-line exact fragment."},
                "start": {"type": "integer", "minimum": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20, "description": "Matches per page."},
                "count_only": {"type": "boolean", "description": "Return only the exact count from start."},
            },
            "required": ["path", "text"],
        },
    },
    {
        "name": "harness_code_map",
        "description": "Lexical code map: find where words from the query are declared, "
                       "one best location per source file (offline, via git grep/rg).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Words from the code, e.g. 'discount rate'."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "description": "Files to show."},
                "cwd": {"type": "string", "description": "Repository root, default: server cwd."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "harness_metrics",
        "description": "Summarize telemetry windows. Supply a local path or inline data. Set summary for alert counts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "data": {"type": "object", "description": "Inline series with windows and samples."},
                "path": {"type": "string", "description": "Local JSON series file."},
                "root": {"type": "string", "description": "Optional allowed root for path."},
                "threshold": {"type": "number"},
                "summary": {"type": "boolean", "description": "Return counts in place of individual alerts."},
            },
        },
    },
]


def resolve_path(raw, root):
    path = Path(raw)
    if root:
        base = Path(root).resolve()
        resolved = path.resolve()
        if not resolved.is_relative_to(base):
            raise ValueError(f"Path outside the allowed root {base}: {path}")
        path = resolved
    if not path.is_file():
        raise ValueError(f"Expected an existing file: {path}")
    return path


def call_tool(name, arguments):
    args = {} if arguments is None else arguments
    if not isinstance(args, dict):
        raise ValueError("Tool arguments must be an object.")
    if name == "harness_context_budget":
        text = json.dumps(history.budget(**args))
    elif name == "harness_view":
        path = resolve_path(args["path"], args.get("root"))
        fields = args.get("fields", [])
        if not isinstance(fields, list) or not all(isinstance(f, str) for f in fields):
            raise ValueError("fields must be an array of names.")
        text = view.render(path, tuple(fields), args.get("base"), receipt=args.get("receipt"),
                           start=args.get("start"), lines=args.get("lines"))
    elif name == "harness_find":
        path = resolve_path(args["path"], args.get("root"))
        text, _ = view.excerpts(path, args["text"], args.get("start", 1),
                                args.get("limit", 5), count_only=args.get("count_only", False))
    elif name == "harness_code_map":
        cwd = Path(args.get("cwd") or os.getcwd())
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            limit = args.get("limit", 10)
            if type(limit) is not int or not isinstance(args.get("query"), str):
                raise ValueError("Expected a text query and integer limit.")
            status = context.code_map(cwd, args["query"], limit)
        text = buffer.getvalue()
        if status:
            text = (text or "") + f"(no matches, exit code {status})"
    elif name == "harness_metrics":
        if ("data" in args) == ("path" in args) or ("root" in args and "path" not in args):
            raise ValueError("Supply exactly one of data or path. Use root with path.")
        data = metrics.read_series(resolve_path(args["path"], args.get("root"))) if "path" in args else args["data"]
        result = metrics.summarize(data, args.get("threshold", 0), summary=args.get("summary", False))
        text = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    else:
        raise ValueError(f"Unknown tool: {name}")
    return [{"type": "text", "text": text}]


def handle(message):
    # main() forwards the result or error in the JSON-RPC response.
    method = message.get("method")
    params = message.get("params", {})
    if not isinstance(params, dict):
        return {"error": {"code": -32602, "message": "Expected object parameters"}}
    if method == "ping":
        return {"result": {}}
    if method == "initialize":
        requested = params.get("protocolVersion", "2025-06-18")
        version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return {"result": {"protocolVersion": version,
                           "capabilities": {"tools": {}},
                           "serverInfo": {"name": NAME, "version": VERSION}}}
    if method == "tools/list":
        return {"result": {"tools": TOOLS}}
    if method == "tools/call":
        try:
            content = call_tool(params.get("name"), params.get("arguments"))
            return {"result": {"content": content, "isError": False}}
        except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
            return {"result": {"content": [{"type": "text", "text": str(exc)}], "isError": True}}
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    return {"error": {"code": -32601, "message": f"Method not found: {method}"}}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None,
                     "error": {"code": -32700, "message": "Parse error"}}
        else:
            if not isinstance(message, dict) or not isinstance(message.get("method"), str):
                reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
            else:
                try:
                    result = handle(message)
                except Exception as exc:
                    result = {"error": {"code": -32603, "message": f"Internal error: {exc}"}}
                if result is None or message.get("id") is None:
                    continue
                reply = {"jsonrpc": "2.0", "id": message["id"], **result}
        sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
