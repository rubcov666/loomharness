# LoomHarness integrations

Use compact reads, local metrics and context budgeting in MCP-capable agents. Lifecycle hooks register directly with supported CLIs or use a bridge. The board is available separately through the local CLI.

| Client | Setup | Edit checks | Prompt hints | Coverage |
|---|---|---|---|---|
| Codex CLI | `install.py --cli codex` | Full | Full | Installer and hooks exercised in CI |
| Codex Desktop | Shares `~/.codex` with the CLI | Full | Full | Documented by OpenAI |
| Claude Code | `install.py --cli claude` | Full for Edit, Write and MultiEdit | Full | Simulated lifecycle events |
| Claude Desktop Code tab | Shares `~/.claude` with the CLI | Full | Full | Documented by Anthropic |
| Gemini CLI | `install.py --cli gemini` | Full for write_file and replace | Full | Simulated lifecycle events |
| Antigravity desktop | `install.py --cli antigravity` | Full mapping | Startup and stop | Based on vendor documentation, live verification pending |
| OpenCode | `integrations/opencode` | Bridge | Bridge | Prompt delivery observed on OpenCode 1.18.0 for macOS. CI covers 1.18 and 2.4 contracts. |
| DeepSeek Harness | `SKILL.md` and `integrations/dsh` MCP bundle | No local hook | Skill | MCP bundle configuration |
| Claude Desktop chat | `claude_desktop_config.json` (MCP) | — | — | Manual MCP configuration |
| Gemini Desktop chat | Remote MCP over HTTPS | — | — | Requires an HTTP-to-stdio wrapper |
| Other MCP agents | `mcp_server.py` | — | — | File reads, search, maps, metrics and context budget |

Codex Desktop and Claude Desktop Code use the same local command-hook settings as their CLIs. One installation serves both. [Codex hooks](https://developers.openai.com/codex/hooks) · [Claude Code hooks](https://code.claude.com/docs/en/hooks) · [Antigravity hooks](https://antigravity.google/docs/hooks)

## Long sessions

For native compaction in Codex, Claude Code or Gemini CLI, an MCP budget decision or rebuilding history in a custom client, see [history compaction](../loomharness/references/history.md). The basic adapter needs the active model's window and input size.

## OpenCode

Install the core with `install.py`. Select this repository's bridge directory in the project configuration. OpenCode 1.18 uses `"plugin": ["file:///absolute/path/to/integrations/opencode"]`. OpenCode 2.x uses `"plugins": [{"package": "./integrations/opencode"}]` with a path relative to the configuration. [OpenCode migration guide](https://opencode.ai/docs/build/plugins/migrate-v1)

Send `LoomHarness on` in the project. To diagnose the edit hook, an edit to `state.json` before an evidence read should give the reason it was blocked. Run `harness.py read <log-path>` in the project with `CODEX_THREAD_ID` set to the current OpenCode session ID, then retry the edit in that session.

By default the bridge calls `~/.agents/skills/loomharness/scripts/harness.py`. Set `LOOMHARNESS_HELPER` for another location and `LOOMHARNESS_PYTHON` for another interpreter.

## DeepSeek Harness

- Copy `loomharness/` with `SKILL.md` into the preset's skill directory.
- The `integrations/dsh` bundle exposes the five file-view, search, code-map, metrics and context-budget tools. Replace `<HELPER>` in `cordis.patch.yml` with the absolute path to `scripts/mcp_server.py`, then install through Plugins → Add plugin.
