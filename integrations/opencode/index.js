// OpenCode 1 and 2 use different hook signatures for the same local Python core.
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import os from "node:os";
import path from "node:path";

const PYTHON = process.env.LOOMHARNESS_PYTHON || (process.platform === "win32" ? "python" : "python3");

function helperPath() {
  if (process.env.LOOMHARNESS_HELPER) return process.env.LOOMHARNESS_HELPER;
  const candidate = path.join(os.homedir(), ".agents", "skills", "loomharness", "scripts", "harness.py");
  return existsSync(candidate) ? candidate : null;
}

// Каноническое событие -> Python-ядро -> решение (null, deny или additionalContext).
function runCore(event) {
  const helper = helperPath();
  if (!helper) return null;
  const result = spawnSync(PYTHON, ["-I", "-X", "utf8", helper, "hook", "--cli", "opencode"], {
    cwd: event.cwd,
    input: JSON.stringify(event),
    encoding: "utf8",
    timeout: 8000,
    windowsHide: true,
  });
  if (result.status !== 0 || !result.stdout || !result.stdout.trim()) return null;
  try {
    return JSON.parse(result.stdout.trim());
  } catch {
    return null;
  }
}

function denyReason(decision) {
  const specific = decision && decision.hookSpecificOutput;
  if (specific && specific.permissionDecision === "deny") return specific.permissionDecisionReason || "denied by LoomHarness";
  if (decision && decision.decision === "deny") return decision.reason || "denied by LoomHarness";
  return null;
}

function checkEdit(cwd, sessionID, tool, input) {
  if (!["edit", "write", "multiedit", "apply_patch"].includes(tool)) return;
  const args = input || {};
  const toolInput = tool === "apply_patch"
    ? { command: args.patchText || args.command || "" }
    : { ...args, file_path: args.filePath || args.file_path || args.path };
  const decision = runCore({
    hook_event_name: "PreToolUse", cwd, session_id: sessionID,
    tool_name: tool === "apply_patch" ? "apply_patch" : "edit_file", tool_input: toolInput,
  });
  const reason = denyReason(decision);
  if (reason) throw new Error(reason);
}

function promptHint(cwd, sessionID, prompt) {
  const decision = runCore({ hook_event_name: "UserPromptSubmit", cwd, session_id: sessionID, prompt });
  return decision?.hookSpecificOutput?.additionalContext;
}

export const LoomHarnessPlugin = {
  id: "loomharness",

  // OpenCode 1.18 passes the arguments and message parts in a separate output object.
  async server({ directory }) {
    return {
      "tool.execute.before": async (input, output) => {
        checkEdit(directory, input.sessionID, input.tool, output.args);
      },
      "chat.message": async (input, output) => {
        const parts = output.parts.filter(p => p.type === "text" && !p.synthetic && !p.ignored);
        if (!parts.length) return;
        const hint = promptHint(directory, input.sessionID, parts.map(p => p.text).join("\n"));
        if (hint) parts[0].text = hint + "\n\n" + parts[0].text;
      },
    };
  },

  // OpenCode 2 registers callbacks during setup and supplies one mutable event.
  async setup(ctx) {
    const cwd = ctx.location.directory;
    await ctx.tool.hook("execute.before", event => {
      checkEdit(cwd, event.sessionID, event.tool, event.input);
    });
    await ctx.session.hook("prompt", event => {
      const prompt = event.prompt.text || "";
      const hint = promptHint(cwd, event.sessionID, prompt);
      if (hint) event.prompt.text = hint + "\n\n" + prompt;
    });
  },
};

export default LoomHarnessPlugin;
