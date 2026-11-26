# LoomHarness

**English** · [Русский](SIMPLE.md) · Version **1.1.3**

LoomHarness helps AI assistants save tokens on any type of task, from coding and research to working with documents and data. It selects relevant content from files, logs and tables locally while keeping the full source available. It also helps reuse previous checks and organize work across agents.

## Token and cost savings

Two sample tasks in Codex compared sending full files with sending only the relevant data. Every answer passed the same checks.

| Model | Total tokens, without → with LoomHarness | Estimated API cost, without → with LoomHarness |
|---|---:|---:|
| GPT-6.2 Sol | 75,214 → 35,300 (**−53%**) | $0.0974 → $0.0173 (**−82%**) |
| GPT-6.2 Astra | 75,091 → 35,176 (**−53%**) | $0.3896 → $0.0501 (**−87%**) |

Costs use OpenAI API Standard rates and account for cached input. Per million uncached input and output tokens respectively, Sol costs $2 and $10, and Astra costs $8 and $40. [Full measurements and rates](docs/benchmarks.md#whole-task-token-usage-in-codex)

## Disk space

In a Windows sample with one active project, LoomHarness used **1.79 MiB** on disk. The installed toolkit used **800 KiB**, project data **998 KiB**, and shared profile files **32 KiB**. Saved logs and caches can increase usage. [Measurement method](docs/benchmarks.md#disk-footprint)

## Install with an AI assistant

Copy this prompt to an assistant with terminal access.

```text
Install and configure LoomHarness for this project. Detect my OS and the CLI I'm using.
Download the main branch from https://github.com/rubcov666/loomharness. If you cannot access the repository, ask me for its ZIP.
Follow integrations/README.md and use Python 3.11 or later. Choose the documented setup for this client.
For a CLI without a native adapter, connect loomharness/scripts/mcp_server.py through stdio MCP if supported. Otherwise, use the Python tools directly from the shell.
Verify the setup for this project and report what works. If a trust prompt or activation requires my input, show me that exact step.
```

### Supported CLI clients

Codex CLI · Claude Code · Gemini CLI · OpenCode · Antigravity CLI (experimental) · DeepSeek Harness (MCP)

## Tools at a glance

| Tool | What it does |
|---|---|
| `view.py` | Finds matching lines and reads the fields you need. |
| `context.py run` | Saves full command output and shows the key errors. |
| `context.py map` | Points to relevant files and source lines. |
| `records.py` · `metrics.py` | Filters records and summarizes counts, totals and alerts. |
| `context.py reuse` · `context.py build-check` | Checks whether earlier results still apply. |
| `history.py budget` | Helps decide when to summarize a long chat. |
| `team.py` · `team.py usage` | Tracks owners, tasks, reviews and token use. |
| `harness.py doctor` · `context.py audit` | Checks hook setup and finds repeated instructions. |

[Command reference](loomharness/references/commands.md) · [Team setup](loomharness/references/adaptive-team.md#first-team-model-choice) · [MIT license](LICENSE)
