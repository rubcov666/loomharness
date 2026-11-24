// Exercise the actual code-mode examples against a catalog and Desktop replies.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

function examples(file) {
  const doc = fs.readFileSync(path.join(__dirname, "../loomharness/references", file), "utf8");
  return Array.from(doc.matchAll(/^`{3}js\r?\n([\s\S]*?)^`{3}\s*$/gm), match => match[1]);
}

const [search, select] = examples("context.md").map(code => new Function("ALL_TOOLS", "text", code));
const list = {name: "mcp__codex_app__list_threads", description: "list threads with a cursor"};
const catalog = [list, ...Array.from({length: 12}, (_, i) => ({
  name: "list_other_threads_" + i, description: "Long schema ".repeat(500)
}))];
search(catalog, result => {
  assert.equal(result.total, 13);
  assert.equal(result.names.length, 8);
  assert.equal(result.names[0], list.name);
  assert.ok(result.names.every(name => typeof name === "string"));
});
search([{name: "catalogue", description: "list editorial threads"}], result => {
  assert.deepEqual(result.names, ["catalogue"]);
});
select(catalog, result => assert.deepEqual(result, list));
select([], result => assert.deepEqual(result, {missing: list.name}));

const waitCode = fs.readFileSync(path.join(__dirname, "../loomharness/scripts/record_wait.js"), "utf8");
const recordWait = new Function(waitCode + "\nreturn recordWait;")();
const sent = [{threadId: "editor", assignment: "review-copy"}];
const settled = new Map();
const poll = {thread: {id: "editor", status: {type: "idle"}}, latestTurn: {status: "completed"}, cursor: "seen"};
recordWait({polls: [poll], wake: {reason: "turnCompleted"}}, sent, settled, 100);
assert.equal(settled.get("review-copy"), 100);
for (const reply of [
  {polls: [{...poll, latestTurn: {status: "failed"}}]},
  {polls: [{...poll, thread: {id: "editor", status: {type: "active", activeFlags: ["waitingOnApproval"]}}}]},
  {polls: [poll], wake: {reason: "requiresInput"}},
  {polls: [poll], errors: [{message: "unavailable"}]},
  {message: "interrupted"}
]) {
  settled.set("review-copy", 100);
  recordWait(reply, sent, settled, 110);
  assert.equal(settled.size, 0);
}
console.log("OK: short discovery, full exact schemas, completion and attention events");
