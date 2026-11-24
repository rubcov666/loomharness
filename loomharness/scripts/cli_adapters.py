"""Map client lifecycle hooks to LoomHarness's canonical event and decision format.

The canonical event follows the Codex format:
  hook_event_name: SessionStart | UserPromptSubmit | PreToolUse | Stop
  cwd, session_id, prompt (UserPromptSubmit), tool_name/tool_input (PreToolUse),
  stop_hook_active, last_assistant_message (Stop).

Client conventions documented in November 2026:
- Codex passes canonical events and blocks tools through
  hookSpecificOutput.permissionDecision="deny".
- Claude Code uses the same event names and Edit/Write/MultiEdit/NotebookEdit
  tools, and supports permissionDecision="deny".
- Gemini CLI uses BeforeAgent/BeforeTool/AfterAgent and write_file/replace.
  It blocks with {"decision": "deny", "reason": ...} and supplies context in
  hookSpecificOutput.additionalContext.
- The OpenCode and DSH integrations supply canonical events from their bridges.
  They receive canonical JSON decisions and translate these for their
  respective clients.
"""

GEMINI_EVENTS = {
    "SessionStart": "SessionStart",
    "BeforeAgent": "UserPromptSubmit",
    "BeforeTool": "PreToolUse",
    "AfterAgent": "Stop",
    "AfterTool": "PostToolUse",
}
# Antigravity uses SessionStart, PreToolUse and Stop.
# Its administrative PreInvocation and PostInvocation events have no
# corresponding canonical event.
ANTIGRAVITY_EVENTS = {
    "PreInvocation": "Other",
    "PostInvocation": "Other",
}
# Client file-edit tools covered by evidence checks.
EDIT_TOOLS = {
    "claude": {"Edit", "Write", "MultiEdit", "NotebookEdit"},
    "gemini": {"write_file", "replace"},
    # Antigravity currently follows Gemini's documented edit-tool names.
    # Add observed names here if the integration diverges.
    "antigravity": {"write_file", "replace"},
}
EDIT_TOOL_CANONICAL = "edit_file"


def detect(raw):
    # BeforeAgent and BeforeTool identify Gemini. Codex and Claude Code use
    # compatible hookSpecificOutput fields, so autodetection can emit the
    # Codex response format for either.
    if raw.get("hook_event_name") in GEMINI_EVENTS and raw.get("hook_event_name") != "SessionStart":
        return "gemini"
    return "codex"


def active_usage(raw, cli):
    """A bridge may attach the latest host observation to a prompt event."""
    usage = raw.get("context_usage")
    if isinstance(usage, dict):
        return {key: usage[key] for key in ("window_tokens", "used_tokens", "protected_tokens",
                                            "trigger_cap", "target_tokens", "remaining_calls",
                                            "compaction_tokens", "reread_tokens") if key in usage}
    if cli == "codex":
        usage = raw.get("tokenUsage")
        if isinstance(usage, dict) and isinstance(usage.get("last"), dict):
            return {"window_tokens": usage.get("modelContextWindow"), "used_tokens": usage["last"].get("inputTokens")}
    if cli == "claude":
        window = raw.get("context_window")
        usage = window.get("current_usage") if isinstance(window, dict) else None
        if isinstance(usage, dict) and "input_tokens" in usage:
            parts = [usage.get(k, 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")]
            if all(type(n) is int and n >= 0 for n in parts) and any(parts):
                return {"window_tokens": window.get("context_window_size"), "used_tokens": sum(parts)}
    if cli == "gemini" and isinstance(raw.get("usageMetadata"), dict):
        return {"window_tokens": raw.get("window_tokens"), "used_tokens": raw["usageMetadata"].get("promptTokenCount")}
    return None


def normalize(raw, cli):
    """Return a canonical event and the client that supplied it."""
    if cli == "auto":
        cli = detect(raw)
    event = dict(raw)
    name = event.get("hook_event_name", "")
    if cli == "gemini":
        event["hook_event_name"] = GEMINI_EVENTS.get(name, "Other")
        if name == "BeforeAgent":
            prompt = event.get("prompt") or event.get("user_prompt") or ""
            event["prompt"] = prompt
    if cli == "antigravity":
        event["hook_event_name"] = ANTIGRAVITY_EVENTS.get(name, name)
    if event.get("hook_event_name") == "UserPromptSubmit":
        event["context_usage"] = active_usage(raw, cli)
        event["client"] = cli
    if event.get("hook_event_name") == "PreToolUse":
        tools = EDIT_TOOLS.get(cli, set())
        if event.get("tool_name") in tools:
            event["tool_name"] = EDIT_TOOL_CANONICAL
    return event, cli


def to_gemini(result):
    """Translate a canonical decision into Gemini CLI's output format.

    Antigravity uses the same deny/ask response format."""
    specific = result.get("hookSpecificOutput")
    if not specific:
        return result
    if specific.get("permissionDecision") == "deny":
        return {"decision": "deny", "reason": specific.get("permissionDecisionReason", "")}
    converted = {}
    if specific.get("additionalContext"):
        converted["hookSpecificOutput"] = {"additionalContext": specific["additionalContext"]}
    if result.get("systemMessage"):
        converted["systemMessage"] = result["systemMessage"]
    return converted or result


def emit(result, cli):
    """Emit the hook decision in the selected client's format."""
    if result is None:
        return
    if cli in ("gemini", "antigravity"):
        result = to_gemini(result)
    import json
    import sys
    print(json.dumps(result, ensure_ascii=True))
