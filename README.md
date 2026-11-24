# LoomHarness

**English** · [Russian](SIMPLE.md)

LoomHarness gives a coding agent the error, source line or count it needs and keeps the full output on disk. In an eight-request replay on synthetic build logs, reading matching lines saved **3.69 million tokens**. Both approaches produced the same final report. [Measured results](#token-benchmarks)

**Version 1.0.0** · Python 3.11+ · Windows, macOS and Linux · **[Download ZIP](https://github.com/rubcov666/loomharness/releases/download/v1.0.0/loomharness-1.0.0.zip)**

## What to use it for

1. **Send fewer tokens to the model.** Return the failing step from a build or the requested fields from a report. The full file stays available for follow-up questions.
2. **Find the cause faster.** Jump from a diagnostic to the matching log lines or to the file and line where a function is declared.
3. **Keep long investigations within the context window.** Estimate when summarizing earlier turns will save tokens over the remaining requests.
4. **Reuse work that still applies.** Check whether inputs and outputs have changed before repeating a build or analysis.
5. **Coordinate independent work on larger projects.** Keep a small fix in one chat. For a larger project, assign owners to subprojects, choose models by stage and track review and token usage across agents.

## Try it in a minute

Download the ZIP above, extract it and open a terminal in the folder containing `install.py`. For Codex, run this with Python 3.11 or later. On Windows, use `python` in place of `python3`.

```sh
python3 -X utf8 install.py --cli codex
```

Open `/hooks` in Codex and approve the four LoomHarness entries on first setup. Send `LoomHarness on` in your project chat. Try asking “Find the first failure in the build log and show me the lines that explain it.”

The ZIP contains the installer, tools, integrations and documentation. Installation uses the Python standard library. The project's own tests run in GitHub Actions for maintainers. [CI workflow](https://github.com/rubcov666/loomharness/blob/main/.github/workflows/verify.yml)

## Token benchmarks

### Measured over eight requests

Twelve synthetic build logs contained the limits, exceptions, source references and retry rule needed for one report. The replay counted cumulative input and output, including repeated history, summaries and a follow-up search. [Replay method and exact counts](docs/benchmarks.md#eight-request-build-investigation)

| How the logs were read | Tokens used (millions) | Tokens saved vs. full logs (millions) |
|---|---:|---:|
| Carry full logs into later requests | 3.704577 | — |
| Read full logs and summarize older turns twice | 1.854089 | 1.850488 |
| Search for decisions and look up the retry rule | 0.011514 | **3.693063** |

### If each CLI task ran 1,000 times

The six tasks below were measured individually with the original 1.0.0 tool messages. For this projection, each measured output is multiplied by 1,000, assuming identical inputs and output lengths each time. Across 6,000 such executions, tool output would fall from **15.008 million to 1.346 million tokens**, saving **13.662 million (91.0%)**. [Measured counts per task](docs/benchmarks.md#six-tool-output-measurements)

| Task × 1,000 | Baseline (million tokens) | LoomHarness (million tokens) | Change |
|---|---:|---:|---:|
| Classify five test logs | 8.391 | 0.316 | -96.2% |
| Locate a definition | 1.455 | 0.081 | -94.4% |
| Read status for 60 jobs | 3.956 | 0.746 | -81.1% |
| Count 24 matching lines | 0.228 | 0.012 | -94.7% |
| Count alerts in 120 samples | 0.786 | 0.098 | -87.5% |
| Read totals for four agents | 0.192 | 0.093 | -51.6% |
| **All 6,000 executions** | **15.008** | **1.346** | **−91.0%** |

In a separate 200,000-line log, the answer “200 matches” took **12 tokens**, down from **542** with excerpts. For a 100,000-line log, the same preview took **40.9 ms** with the optimized reader, down from **178.0 ms** (median of 21 paired runs on macOS). [Search measurement](docs/benchmarks.md#searching-200000-lines) · [Timing method](docs/benchmarks.md#previewing-100000-lines)

## Tool guide

| Tool | What it does and when to use it |
|---|---|
| `context.py run` | Runs a command, saves stdout and stderr, and returns its exit code and key diagnostics. Use it for noisy builds and test runs. |
| `view.py` · `harness_view` · `harness_find` | Reads numbered lines or selected JSON fields. Literal search returns excerpts with context or just a match count. Use it to answer a question about one file. |
| `context.py map` · `harness_code_map` | Ranks candidate files and reports paths and line numbers for a query. For a Java method and its overloads, `context.py symbol` uses the optional Tree-sitter parser. |
| `history.py budget` · `harness_context_budget` | Weighs the cost of a summary and rereading details against carrying history into future requests. Use it to decide when to compact. |
| `context.py reuse` · `context.py build-check` | The first compares fingerprints of inputs and saved results. The second matches recorded checks to a specific build. Use them before repeating work. |
| `metrics.py --summary` · `harness_metrics` | Reads time-series data locally and returns coverage, missing samples, the peak and alert counts. Use it to investigate a threshold from a large report. |
| `team.py` · `team.py usage` | Tracks task owners, handoffs and reviews. Its usage report collects recorded input, cached input and output for the selected chat and its local agents. |
| `context.py audit` · `harness.py doctor` | The audit finds repeated instructions and lists configured MCP servers. The doctor reports hook registration and observed events. Use them when troubleshooting your setup. |

## Other clients

The installer also supports Claude Code and Gemini CLI. OpenCode has a plugin. In a stdio MCP client, set the command to your Python executable and the argument to the absolute path to `loomharness/scripts/mcp_server.py`. The five `harness_*` tools in the table are available through MCP. [Client setup and support](integrations/README.md)

[Command reference](loomharness/references/commands.md) · [Context and history](loomharness/references/history.md) · [Working with agents](loomharness/references/team.md) · [MIT license](LICENSE)
