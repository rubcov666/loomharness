# Tool-output token benchmarks

The benchmark compares two ways of answering each question on the same generated input. It counts the UTF-8 text returned to the agent with the `o200k_base` tokenizer, including trailing newlines and summaries. It runs the public CLI commands against a fresh temporary directory and Git repository. All test data are generated locally.

## Reproduce the table

Use Python 3.11+ and Git. From the root of a checkout, run the following in a Python environment of your choice.

```sh
python -m pip install tiktoken==0.14.0
python tests/measure_tool_tokens.py
```

The script prints the Markdown table in the [README](../README.md#token-benchmarks). The harness itself uses the standard library for these commands.

## What each pair measures

| Question | Baseline | LoomHarness | Answer checked |
|---|---|---|---|
| What happened in five test runs? | Five full logs and their exit codes | Five `context.py run` responses, including the saved-log locations and any follow-up needed to read the cause | Successful, empty and skipped runs, failing test and final reason code |
| Where is a pricing function declared? | Every `git grep -n` hit for the name | `context.py map` | Source file and declaration line |
| What is the status of every job? | Complete JSON for 60 jobs | `view.py --fields id checks` | All 60 rows in order, including the failed row and nested diagnostic |
| How many lines contain a retry? | The default `view.py --find` response with matching excerpts | The same search with `--count` | 24 matching lines out of 240 |
| How many samples reached the threshold? | All alerts from `metrics.py` | `metrics.py --summary` | 30 alerts among 120 samples, same coverage and peak |
| What are the agents’ combined totals? | Full `team.py usage` with participant breakdown | The same command with `--summary` | Four participants, identical token totals and coverage |
| What does one short line say? | Raw `OK` plus a newline | A numbered one-line `view.py` response | The same text and its line number |

The change column uses `(LoomHarness − baseline) / baseline`. The token count can grow when the tool adds a line number or other context to an already small result. A targeted shell command such as `rg -c` can also answer a count question in a few tokens.

## Measuring a whole task

Tool output is one contributor to model input. To study session usage, run the same task with the same model, starting revision and acceptance criteria. Count all participating agents and check their answers. `team.py usage --thread ID --since START --until END` reports input, cached input and output separately for an interval. Model billing also depends on the provider and cache pricing. [Telemetry reference](../loomharness/references/telemetry.md)
