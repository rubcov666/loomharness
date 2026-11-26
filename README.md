# LoomHarness

**English** · [Русский](SIMPLE.md)

LoomHarness helps AI assistants work with large files, logs and tables. It finds relevant lines and values locally, so the model reads fewer tokens and the full source stays available. It also helps reuse previous checks and organize work across agents.

## Token and cost savings

Two sample tasks in Codex compared sending full files with sending only the relevant data. Every answer passed the same checks.

| Model | Total tokens, without → with LoomHarness | Estimated API cost, without → with LoomHarness |
|---|---:|---:|
| GPT-6.2 Sol | 75,214 → 35,300 (**−53%**) | $0.0974 → $0.0173 (**−82%**) |
| GPT-6.2 Astra | 75,091 → 35,176 (**−53%**) | $0.3896 → $0.0501 (**−87%**) |

Costs use OpenAI API Standard rates and account for cached input. Per million uncached input and output tokens respectively, Sol costs $2 and $10, and Astra costs $8 and $40. [Full measurements and rates](docs/benchmarks.md#whole-task-token-usage-in-codex)

## Start with Codex

Requires **Python 3.11+** on Windows, macOS or Linux.

1. [Download the current ZIP](https://github.com/rubcov666/loomharness/archive/refs/heads/main.zip) and extract it.
2. In the folder with `install.py`, run `python -X utf8 install.py --cli codex`. Use `python3` if that is your Python command.
3. If Codex asks you to review the hooks, open a system terminal and run `codex`. In **Codex CLI**, enter `/hooks` and trust the four LoomHarness entries. For Codex Desktop, use the CLI with the same Codex configuration. [Codex hook setup](https://learn.chatgpt.com/docs/hooks#review-and-trust-hooks)
4. In your project chat, send `LoomHarness on` and start your task.

[Setup for Claude Code, Gemini CLI, OpenCode and other clients](integrations/README.md)

### Install with an AI assistant

Copy this prompt to an assistant with terminal access.

```text
Install and configure LoomHarness for this project.
Download the main branch from https://github.com/rubcov666/loomharness.
Use README.md and integrations/README.md to choose the setup for my OS and client.
Run the installation, verify it, and complete the project setup where you can.
If you need me to approve hooks or send an activation message, show me the exact step.
For Codex hook approval, direct me to /hooks inside Codex CLI.
If the private repository is inaccessible, ask me for the ZIP.
```

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
