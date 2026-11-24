# LoomHarness benchmarks

## Six tool-output measurements

The six CLI tasks in the [README](../README.md#token-benchmarks) use generated data. Each pair asks the same question twice and checks the answer. The runner counts the UTF-8 text the tool returns, including diagnostics and trailing newlines, using `o200k_base`. The baseline outputs total 15,008 tokens. The LoomHarness outputs total 1,346. The 91.0% reduction is calculated from these totals.

For an exact reproduction, use Python 3.11+ and Git with the source revision below. It contains the original 1.0.0 diagnostics used in these counts. Later English wording can produce different token counts for the same result. The benchmark runner is part of the source repository. [Measured source revision](https://github.com/rubcov666/loomharness/tree/db9155686d651485da10c1d61d5c65ffe7464cff)

```sh
git clone https://github.com/rubcov666/loomharness.git loomharness-benchmark
cd loomharness-benchmark
git switch --detach db9155686d651485da10c1d61d5c65ffe7464cff
python -m pip install tiktoken==0.14.0
python tests/measure_tool_tokens.py
```

The script creates a temporary project and Git repository and prints the six-row Markdown table. These LoomHarness commands use the Python standard library. Only the measurement script needs the tokenizer.

| Question | Baseline | LoomHarness | Answer checked | Measured tokens (baseline → LoomHarness) |
|---|---|---|---|---:|
| What happened in five test runs? | Five full logs and their exit codes | Five `context.py run` responses, including the saved-log locations and any follow-up needed to read the cause | Successful, empty and skipped runs, failing test and final reason code | 8,391 → 316 |
| Where is a pricing function declared? | Every `git grep -n` hit for the name | `context.py map` | Source file and declaration line | 1,455 → 81 |
| What is the status of every job? | Complete JSON for 60 jobs | `view.py --fields id checks` | All 60 rows in order, including the failed row and nested diagnostic | 3,956 → 746 |
| How many lines contain a retry? | The default `view.py --find` response with matching excerpts | The same search with `--count` | 24 matching lines out of 240 | 228 → 12 |
| How many samples reached the threshold? | All alerts from `metrics.py` | `metrics.py --summary` | 30 alerts among 120 samples, same coverage and peak | 786 → 98 |
| What are the agents’ combined totals? | Full `team.py usage` with participant breakdown | The same command with `--summary` | Four participants, identical token totals and coverage | 192 → 93 |

The change column uses `(LoomHarness − baseline) / baseline`. A targeted shell command such as `rg -c` can also answer a count question in a few tokens.

### Projecting repeated operations

The README also shows what the six measured outputs would amount to if each task ran 1,000 times with the same input and the same output length. Multiply each per-task count above by 1,000, then divide by 1,000,000 to express it in millions. The projected totals are 15.008 million baseline tokens and 1.346 million LoomHarness tokens across 6,000 executions. The difference is 13.662 million tool-output tokens. Model input, generated replies, cache usage and billing are separate measurements.

## Eight-request build investigation

This earlier offline replay started with five synthetic build logs. Each contained 3,400 lines of build output followed by one decision recording a limit, an exception and its source. Seven further reads brought the total to 12 logs before the final report. A separate retry rule appeared only in the first log.

| History strategy | Cumulative tokens over eight requests |
|---|---:|
| Carry the full logs into each request | 3,704,577 |
| Read the full logs and summarize the older turns twice | 1,854,089 |
| Search for decision records and look up the retry rule when needed | 11,514 |

The replay counted input and output across requests, including repeated history, summary generation and the extra lookup, with `o200k_base` on JSON Lines messages. It used scripted replies. Every strategy had to recover the same limits, exceptions, source references and retry rule. The records had known labels and most log lines contained routine progress. These conditions explain the 99.69% reduction between the first and last rows. The six-task runner above reproduces the current CLI table. The figures here and below are from earlier development measurements.

## Searching 200,000 lines

A generated log contained 200,000 records. One record in every 1,000 contained `state=retry`. The default search page returned the count and matching excerpts in 542 tokens. `view.py --find state=retry --count` returned the same count of 200 in 12 tokens. Both figures measure the text returned by the CLI.

## Previewing 100,000 lines

Two versions of the local file reader displayed the same default preview of a generated 100,000-line log. After one warm-up, 21 paired runs alternated the order of the versions. Each run started a fresh Python process and included CLI startup time. On macOS arm64 with Python 3.11.15, the earlier reader took 178.0 ms at the median and 178.9 ms at p95. The optimized reader took 40.9 ms at the median and 41.4 ms at p95.

## Measuring a whole task

Tool output is one contributor to model input. To study session usage, run the same task with the same model, starting revision and acceptance criteria. Count all participating agents and check their answers. `team.py usage --thread ID --since START --until END` reports input, cached input and output separately for an interval. Model billing also depends on the provider and cache pricing. [Telemetry reference](../loomharness/references/telemetry.md)
