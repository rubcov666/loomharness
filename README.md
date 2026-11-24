# LoomHarness

Give your coding agent the failing test, the source line or the alert count it needs. LoomHarness keeps the full output on disk so the agent can look closer when a summary leaves a question open.

In the included five-log benchmark, the raw outputs took **8,391 tokens**. LoomHarness returned the test results, skip and failure details in **316 tokens**. You can reproduce the comparison locally without a model or an API key. [Benchmark details](docs/benchmarks.md) · [Руководство на русском](SIMPLE.md)

**Version 1.0.0** · Python 3.11+ · Windows, macOS and Linux

## Try it in a minute

You need Python 3.11+ and Git. From the repository root, run a small job that prints the same progress message 100 times.

```sh
git clone https://github.com/rubcov666/loomharness.git
cd loomharness
python3 loomharness/scripts/context.py run --log demo.log -- python3 -c "for i in range(100): print('waiting for worker')"
```

The summary contains `1-100: waiting for worker`. Open `demo.log` to see all 100 original lines. Each run needs a fresh log filename. On Windows, use `python` in place of `python3`.

## Token benchmarks

Counts cover the tool output returned to the agent, including diagnostic text. Each row answers the same question on the same generated data. The baseline is named in the [benchmark protocol](docs/benchmarks.md).

| Task | Baseline tokens | LoomHarness tokens | Change |
|---|---:|---:|---:|
| Classify five test logs | 8,391 | 316 | -96.2% |
| Locate a definition | 1,455 | 81 | -94.4% |
| Read status for 60 jobs | 3,956 | 746 | -81.1% |
| Count 24 matching lines | 228 | 12 | -94.7% |
| Count alerts in 120 samples | 786 | 98 | -87.5% |
| Read totals for four agents | 192 | 93 | -51.6% |
| Read one short line | 2 | 4 | +100.0% |

A two-token line is cheaper to read directly. For a whole model session, measure repeated input, cache hits and output separately. [Usage reporting](loomharness/references/telemetry.md)

## Use it with your agent

**Codex.** Run the installer, open `/hooks` in Codex and approve the four LoomHarness entries. In a project chat, send `LoomHarness on`. Then ask it to run your tests, report failures and keep the full log. The project choice is stored locally.

```sh
python3 -X utf8 install.py --cli codex
```

**Other clients.** Claude Code and Gemini CLI have installer targets. OpenCode has a plugin. A stdio MCP server exposes five tools for file views, search, code maps, metrics and context budgeting. Point your MCP client at your Python executable and the absolute path to `loomharness/scripts/mcp_server.py`. [Client setup and support](integrations/README.md)

## Choose the right tool

| When you need to… | Use |
|---|---|
| Follow a failure through a noisy build | `context.py run` saves the full log and returns the exit code. `view.py --find` retrieves matching lines with surrounding context. |
| Answer a question about a large report | `view.py --fields` selects the fields you need. `metrics.py --summary` returns counts, coverage and the peak. |
| Find an implementation | `context.py map` returns candidate declarations with paths and line numbers. `context.py symbol` reads a Java method and its overloads with the optional Tree-sitter parser. |
| Continue a long task | `history.py budget` estimates when a summary pays off. A project board tracks assignments, submissions and independent review across agents. |

[Command reference](loomharness/references/commands.md) · [Context and history](loomharness/references/history.md) · [Working with agents](loomharness/references/team.md) · [MIT license](LICENSE)
