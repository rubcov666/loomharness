"""Адаптеры lifecycle-хуков разных CLI к каноническому формату LoomHarness.

Канонический формат события совпадает с форматом Codex:
  hook_event_name: SessionStart | UserPromptSubmit | PreToolUse | Stop
  cwd, session_id, prompt (UserPromptSubmit), tool_name/tool_input (PreToolUse),
  stop_hook_active, last_assistant_message (Stop).

Отличия целевых CLI (проверено по документации, ноябрь 2026):
- Codex: канонический формат как есть; блокировка —
  hookSpecificOutput.permissionDecision="deny".
- Claude Code: те же имена событий; инструменты правки Edit/Write/MultiEdit/
  NotebookEdit вместо apply_patch; permissionDecision="deny" поддержан.
- Gemini CLI: события BeforeAgent/BeforeTool/AfterAgent, инструменты
  write_file/replace; блокировка — {"decision": "deny", "reason": ...},
  контекст — hookSpecificOutput.additionalContext.
- opencode и DSH: события уже приходят в каноническом виде из JS-моста
  (integrations/opencode, integrations/dsh); вывод — канонический JSON,
  который мост разбирает сам.
"""

GEMINI_EVENTS = {
    "SessionStart": "SessionStart",
    "BeforeAgent": "UserPromptSubmit",
    "BeforeTool": "PreToolUse",
    "AfterAgent": "Stop",
    "AfterTool": "PostToolUse",
}
# Antigravity (десктопная линейка Google, преемник Gemini CLI) использует
# имена событий SessionStart/PreToolUse/Stop; PreInvocation/PostInvocation —
# служебные, в каноническую модель не переводим.
ANTIGRAVITY_EVENTS = {
    "PreInvocation": "Other",
    "PostInvocation": "Other",
}
# Инструменты правки файлов, подпадающие под гейт доказательств, по CLI.
EDIT_TOOLS = {
    "claude": {"Edit", "Write", "MultiEdit", "NotebookEdit"},
    "gemini": {"write_file", "replace"},
    # Antigravity: формат инструментов унаследован от Gemini CLI (по докам);
    # при расхождении имён добавьте сюда фактические.
    "antigravity": {"write_file", "replace"},
}
EDIT_TOOL_CANONICAL = "edit_file"


def detect(raw):
    # События BeforeAgent/BeforeTool есть только у Gemini CLI. Форматы Codex и
    # Claude Code совпадают, поэтому автоопределение отдаёт им codex-эмиcсию,
    # которая для Claude корректна (те же hookSpecificOutput-поля).
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
    """Привести событие CLI к каноническому формату. Вернуть (event, cli)."""
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
    """Перевести каноническое решение в формат вывода Gemini CLI.

    Тот же стиль вывода использует Antigravity (decision deny/ask)."""
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
    """Вывести решение хука в формате выбранного CLI."""
    if result is None:
        return
    if cli in ("gemini", "antigravity"):
        result = to_gemini(result)
    import json
    import sys
    print(json.dumps(result, ensure_ascii=True))
