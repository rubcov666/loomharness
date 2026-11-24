# When history compaction pays off

Before a request and after a model change, compare active input with the model's context window. Count instructions, tool schemas, history, results and cached input once each. Chat totals span multiple requests. If the window or active size is unknown, ask the client for its reading or use its native policy.

The default trigger is the smaller of 256,000 tokens and 75% of the window. The target is 5/16 of the trigger. For a 1,000,000-token window this gives 256,000 and 80,000. For 128,000 it gives 96,000 and 30,000. Tune the upper bound with `--trigger` and the target with `--target`. When a model change puts the target at or above the new trigger, the proportional target applies.

Preserve exact requirements and exceptions, owner decisions, the open question, IDs and source links. Keep the latest messages and each unfinished tool call with its entire result. Summarize completed work and link its source. After compaction, check these facts and the next input size with the client. Compare equally correct outcomes, including summary and reread costs.

## Accounting for summaries

Send only the data needed for each decision, starting with the first request. Save full output with `context.py run --log` and select relevant content with `view.py --find` or `--fields`. For large repeated reads, `view.py --receipt new` returns a receipt with the data. Reuse it while the previous view remains available to the agent. Retrieve an excerpt again if that view is lost. MCP exposes the same option as `harness_view.receipt`. [Reading sources](context.md#excerpts-and-versions)

At the trigger, give `budget` the expected number of ordinary model calls before the end of the stage or next summary, including the next call. Use zero for a finished answer and one for a final reply. The default estimate is two. `compaction_tokens` covers additional summary input and output. The default allows for the current input plus up to 4,096 summary tokens. If the client sends only a completed segment, use its measured size. `reread_tokens` covers the full extra cost of recovering omitted details, including additional model inputs. Its default is zero.

Savings per call equal the difference between current and projected input. Compact when this difference times remaining calls exceeds summary and recovery costs. Keep history at break-even. For 256,000 → 80,000 with a summary costing 260,096 tokens and zero recovery, two calls pay off. `break_even_calls` is the first profitable call count. At 7/8 of the window, prefer room for the next reply if protected material fits. Use the client log to check estimates and separate cached input and output for billing.

## Native client compaction

Run the commands below from the skill directory. Pass the active client's window size to `--window`. Use `--trigger` for a lower agreed threshold. Launch settings apply only to the child process.

```sh
python scripts/history.py launch --window 1000000 codex
python scripts/history.py launch --window 1000000 claude
python scripts/history.py budget --window 1000000 --used 256000 --client gemini
python scripts/history.py budget --window 1000000 --used 256000 --remaining-calls 1
```

| Client | How the threshold is applied |
|---|---|
| Codex | `launch` sets `model_auto_compact_token_limit` and counts the full active context. [Codex settings](https://learn.chatgpt.com/docs/config-file/config-reference) |
| Claude Code | `launch` sets `CLAUDE_CODE_AUTO_COMPACT_WINDOW` for the process. The client accepts absolute windows from 100,000 to 1,000,000. Use the portable mode for lower thresholds. [Claude Code settings](https://code.claude.com/docs/en/model-config#set-the-auto-compact-window) |
| Gemini CLI | Add the `adapter.settings` returned by `budget` to `.gemini/settings.json` and restart the CLI. A million-token window uses `model.compressionThreshold` of 0.256. Recalculate when the window changes. [Gemini CLI settings](https://geminicli.com/docs/reference/configuration/) |

The native client generates the summary and sets its final size. For clients with one threshold, `launch` and `budget --client` calculate a fixed trigger from the initial estimates. Pass `--remaining-calls`, `--compaction-tokens` and `--reread-tokens` before the client name in `launch`. Check existing project settings. Update the threshold through the client or next launch when the model or workload changes.

## MCP or CLI integration

Call `harness_context_budget` through MCP or `history.py budget` on the CLI. Supply `window_tokens`, `used_tokens`, verbatim `protected_tokens` and available estimates for `remaining_calls`, `compaction_tokens` and `reread_tokens`. `keep` retains history, `measure` requests missing data, and `compact` gives its reason. For `preserve` keep the original material and account for the stated constraint.

An event bridge can attach these counters and estimates as `context_usage` on `UserPromptSubmit` and call `harness.py hook --cli portable`. An enabled project receives a hint when the decision changes to `compact`. Adapters also accept Codex `tokenUsage.last.inputTokens` with `modelContextWindow`, Claude Code `context_window.current_usage` and Gemini `usageMetadata.promptTokenCount` with `window_tokens` when exposed by the client. The host manages message history after an MCP response.

## Clients that own the message list

Save the original snapshot locally. Preserve client message roles, image blocks and tool IDs. Put stable instructions and tools in `prefix` and group the rest into `turns`. Each block has `messages` and `tokens` measured for the chosen model, including protocol overhead. Keep a tool call with all its results. Marking a group `"keep": true` protects that group and all later groups. The last group is always protected.

If the SDK supplies instructions or tools in separate fields, leave them with the host and count them in `prefix.tokens`. In that case `prefix.messages` may be empty. Set `prefix.revision` to detect changed instructions and schemas. After `replace` populate the host's history field, such as OpenAI Responses `input`, Anthropic `messages` or Gemini `contents`.

```json
{
  "window_tokens": 1000000,
  "prefix": {"tokens": 10000, "messages": [{"role": "system", "content": "Project instructions"}]},
  "turns": [
    {"tokens": 200000, "messages": [{"role": "user", "content": "Completed stage materials"}]},
    {"tokens": 50000, "messages": [{"role": "user", "content": "Current question and evidence"}]}
  ]
}
```

The example counts are illustrative. Add cost estimates at the snapshot root. A completed group can name verbatim `anchors` from its messages, such as an exception and its source. Mark material needed in full with `keep`.

`python scripts/history.py plan snapshot.json` accounts for whole-group boundaries. On `compact` it returns `summarize_turns`, `source_sha256` and any `required_anchors`. Pass an existing matching summary directly to `apply`. Otherwise summarize the selected groups into `checkpoint.json` with `source_sha256`, `tokens` and client-format `messages`. Preserve anchors verbatim and use the appropriate source role.

`apply` uses the actual summary size with zero additional generation cost. Missing required text yields `preserve` and `missing_anchors`. Matching checks the summary and stable prefix for exact text. Verify meaning and source attribution separately. For the next cycle, move a verified summary to the stable prefix or carry forward any verbatim anchors in its group.

`python scripts/history.py apply snapshot.json checkpoint.json` verifies the source and builds the next input. Only on `action=replace` send the returned `messages` as the complete history. If the server retains history by ID, use its replacement operation or a fresh session. Keep the original available for recovery. Recount with the new tokenizer when switching models. In closed clients use native compaction or a handoff to a new session. Check that the next request actually uses the smaller history.
