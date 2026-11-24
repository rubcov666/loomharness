import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const temp = mkdtempSync(path.join(tmpdir(), "loom-opencode-"));
process.env.CODEX_HOME = path.join(temp, "profile");
delete process.env.LOOMHARNESS_HOME;
process.env.LOOMHARNESS_HELPER = fileURLToPath(new URL("../loomharness/scripts/harness.py", import.meta.url));
const { default: plugin } = await import("../integrations/opencode/index.js");

try {
  const directory = path.join(temp, "project");
  mkdirSync(directory);
  const attachment = { type: "file", url: "data:text/plain,example" };
  const skipped = { type: "text", text: "system", synthetic: true };
  const message = { type: "text", text: "LoomHarness on" };
  const hooks = await plugin.server({ directory });
  const v1 = { sessionID: "opencode-one" };
  await hooks["chat.message"](v1, { parts: [attachment, skipped, message] });
  assert.match(message.text, /Project hooks enabled\./);
  assert.ok(message.text.endsWith("LoomHarness on"));
  assert.equal(skipped.text, "system");
  assert.equal(attachment.url, "data:text/plain,example");
  await hooks["tool.execute.before"]({ ...v1, tool: "write" }, { args: { filePath: "app.js" } });
  await assert.rejects(
    hooks["tool.execute.before"]({ ...v1, tool: "edit" }, { args: { filePath: "state.json" } }),
    /Before editing state/
  );

  const callbacks = new Map();
  const register = (name, callback) => callbacks.set(name, callback);
  await plugin.setup({ location: { directory }, tool: { hook: register }, session: { hook: register } });
  const v2 = { sessionID: "opencode-two" };
  const prompt = { text: "LoomHarness on", attachments: [attachment] };
  await callbacks.get("prompt")({ ...v2, prompt });
  assert.match(prompt.text, /Project hooks enabled\./);
  assert.ok(prompt.text.endsWith("LoomHarness on"));
  assert.deepEqual(prompt.attachments, [attachment]);
  const before = callbacks.get("execute.before");
  const patch = "*** Begin Patch\n*** Update File: state.json\n@@\n-a\n+b\n*** End Patch";
  assert.throws(() => before({ ...v2, tool: "apply_patch", input: { patchText: patch } }), /Before editing state/);
  before({ ...v2, tool: "write", input: { filePath: "app.js" } });
  writeFileSync(path.join(directory, "build.log"), "build passed\n");
  const python = process.env.LOOMHARNESS_PYTHON || (process.platform === "win32" ? "python" : "python3");
  execFileSync(python, ["-I", "-X", "utf8", process.env.LOOMHARNESS_HELPER, "read", "build.log"], {
    cwd: directory, env: { ...process.env, CODEX_THREAD_ID: v2.sessionID },
  });
  before({ ...v2, tool: "apply_patch", input: { patchText: patch } });
  await assert.rejects(
    hooks["tool.execute.before"]({ ...v1, tool: "edit" }, { args: { filePath: "state.json" } }),
    /Before editing state/
  );
  console.log("OK: OpenCode v1 and v2 prompt and edit hooks with the Python core");
} finally {
  rmSync(temp, { recursive: true, force: true });
}
