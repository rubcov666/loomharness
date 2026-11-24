# LoomHarness

**English** · [Русский](SIMPLE.md)

Give your coding agent the failing test, the source line or the alert count it needs. LoomHarness keeps the full output on disk so the agent can follow up on a summary.

**3,704,577 → 11,514 tokens (−99.69%)** in an offline replay of eight requests against synthetic build logs. One approach carried full logs into each request. The other searched the logs and retrieved a missing retry rule on demand. Both produced the same final report, including every limit, exception and source. [How the replay was counted](docs/benchmarks.md#eight-request-build-investigation)

**Version 1.0.0** · Python 3.11+ · Windows, macOS and Linux

## Try it in a minute

You need Python 3.11+ and Git. Run a small job that prints the same progress message 100 times.

```sh
git clone https://github.com/rubcov666/loomharness.git
cd loomharness
python3 loomharness/scripts/context.py run --log demo.log -- python3 -c "for i in range(100): print('waiting for worker')"
```

The summary contains `1-100: waiting for worker`. Open `demo.log` to see all 100 original lines. Each run needs a fresh log filename. On Windows, use `python` in place of `python3`.

## Token benchmarks

Across six tasks, **15,008 → 1,346 tokens (−91.0%)** of tool output reached the agent. Each pair answers the same question on the same generated data. You can reproduce these six measurements locally without a model or an API key. [Baselines and commands](docs/benchmarks.md#six-tool-output-measurements)

| Task | Baseline tokens | LoomHarness tokens | Change |
|---|---:|---:|---:|
| Classify five test logs | 8,391 | 316 | -96.2% |
| Locate a definition | 1,455 | 81 | -94.4% |
| Read status for 60 jobs | 3,956 | 746 | -81.1% |
| Count 24 matching lines | 228 | 12 | -94.7% |
| Count alerts in 120 samples | 786 | 98 | -87.5% |
| Read totals for four agents | 192 | 93 | -51.6% |

Two earlier measurements show what happens with larger files.

- In a **200,000-line log**, 200 lines matched. Returning just the count took **12 tokens**, down from **542** for the default search page. [Search measurement](docs/benchmarks.md#searching-200000-lines)
- For a **100,000-line log**, the optimized reader returned the same preview in **40.9 ms**, down from **178.0 ms**. Both figures are median CLI times over 21 paired runs on macOS. [Timing method](docs/benchmarks.md#previewing-100000-lines)

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
