# Model selection and reviewing during a background test

Run the commands below via `scripts/team.py --project DIRECTORY` from the role's own chat. Decisions are stored per project.

## One reviewer and a background test

1. A worker can submit with `--quick-review` when a quick check of fixed inputs is sufficient. Declare shared resources with `--resource` on the cards. Reserve separate time for overlapping or unknown scopes. Network requests, builds or live-service changes do not qualify as quick checks.
2. The reviewer takes `job-claim`, checks the exact revision and independently chooses a test. Start a longer test using `review-test --recheck 300 JOB TICKET CARD -- PROGRAM ARGS` with arguments as a list. Exit 0 means that check's criteria passed. The wrapper runs in the project root, forwards output and records the result. Use a background shell session and keep its ID.
3. In `inbox --watch` (inside `board` for `--parts`), the reviewer's active job contains `test` and eligible `short_ready` cards. Choose one that fits the time, run `review-side JOB CARD` and send the brief to the same reviewer chat. The ticket stays valid. On a shared job pass `--task ID` for each verdict. Refresh the snapshot if the side card became stale.
4. While the test runs, wait on `inbox --watch --after CURSOR --wait SECONDS`, chat events and the next `watch.recheck_at`. Use one snapshot per dispatch cycle. Return to the original card when `test.status` is `passed` or `failed`. On pass the reviewer examines the evidence and decides independently. A failed test records its exit code and blocks approval until addressed.
5. At `check` inspect the session. A recheck deadline alone does not establish a failure. When a stopped process has no recorded result, the reviewer or lead runs `review-interrupt JOB TICKET RUN "evidence of stop"`. A late result from the prior run cannot affect a retry.

## Selecting a model for a stage

* For a stage with known inputs and a verifiable answer, use `dispatch ID --stage bounded --work-type TYPE`. Name types by the operation and criterion. Field extraction and diagnosis require different checks.
* At a stage boundary in the same chat, use `next-stage ID --stage bounded|standard|diagnosis|waiting`. Add `--work-type TYPE` for a new operation. Send the next step with the returned `threadId`, `model` and `thinking`. At `waiting` wait for an event. Revision, worker, resources and ticket persist. Account for external test and queue time separately from model work.
* On delay read the worker's last event. Wait for a running external process. Move the next stage to `diagnosis` for a new root cause or conflicting results. For a remaining formal step use `bounded` with its work type. Use the model selected by the board.
* `standard` uses the default model. `diagnosis` and a repeat return after two failed attempts use the stronger model. The default model handles bounded work until a fast model qualifies and takes over bounded cards after a return. Returning a result involving a qualified fast model revokes qualification for that model pair and work type. A strong independent reviewer checks quality.

## First team model choice

For a new team use `team.py model-choice` in the project root. Skip the setup interview when `intake.model_choice` already contains the owner's selection. Add `--codex` to read the available Codex models without starting a model turn. Add `--lang ru` for Russian text. Other clients can pass `--catalog FILE`, a verified JSON array of entries with `id` and optional `label`, `default`, `tier` (`strong` or `fast`), `effort` and `efforts`. Without a catalog, ask the user for an exact model ID they can select in their client. For the current-model preset, confirm the open chat's model if it is unknown.

Use the host's interactive user-input tool whenever it is exposed. Render selectable choices with the host's free-text entry. Use stable option IDs internally and the supplied labels in the UI. If the widget caps the list, offer the most relevant available models and allow any other valid ID through the text field. Keep role questions in one batch when the host permits it. If the host has no form tool, ask the same questions in chat with numbered options and allow typed IDs. Leave the setup pending when the owner skips, cancels or leaves a required question unanswered.

1. When the script returns `choose-preset`, ask the four-choice question from `question` and `options`.
2. Call `model-choice --preset current|balanced|strong|manual` with the same catalog source and known choices. Use `--current MODEL [--current-effort LEVEL]` for the current chat, `--strong MODEL [--worker MODEL]` for balanced or strong, or `--coordinator MODEL --worker MODEL --reviewer MODEL` for manual. Balanced and manual also accept `--routine MODEL` or `--no-routine`. Ask the unresolved items in `questions`. Manual setup asks for all three role models unless the owner has already supplied a choice for a role.
3. When the result is `ready` with `confirmation`, show the role assignments in `policy` and ask the confirmation with its two options. On **Change models**, ask for corrected choices and preview again. On **Confirm selection**, rerun with `--apply` and the exact previewed model IDs, including the routine choice if present. Check `saved` before continuing. A preselected option or a timeout is insufficient. For an existing board, only the coordinator saves changes.
4. Run `init --goal "…"` for a new team. Pass the selected model and supported effort from `dispatch`, `review` or `next-stage` on each new assignment. Set the coordinator's model in the client if its current chat cannot change its own model. Per-assignment model application also depends on the selected execution driver.

From a normal terminal, `team.py --actor OWNER_ID model-choice --interactive` runs the same sequence with numbered choices and typed IDs. Add `--project PROJECT_ROOT`, `--codex` or `--catalog FILE`, and `--lang ru` as needed. Use the actual existing coordinator ID to change an existing board. In another client, forward its session ID with `--actor` when `CODEX_THREAD_ID` is not set. Agent shell tools and headless runs should use the nonblocking JSON output and present the questions through the host.

Refresh the catalog if the client rejects a recorded model and ask about that role. Do not substitute a different model without owner approval. A selected routine model receives formal work after paired qualification. For the current and strong presets, routine switching is disabled.

## Qualifying models on historical tasks

1. Set the routine candidate through the project model choice. A legacy board without a selection can use `.codex/harness-models.json`, for example `{"routine":"model-name"}`. The baseline is the project's worker model. Compare for a new work type or model change. Ordinary dispatch creates no evaluation chats.
2. Prepare accepted and returned revisions with known errors and edge cases. For each case, write a neutrally named immutable project file with task, inputs and criteria. Exclude old verdicts, answers and revealing filenames. In the private manifest list real review-job and card pairs.

   ```json
   [{"job":21,"task":5,"input":"evaluation/sample-a.txt"},{"job":24,"task":6,"input":"evaluation/sample-b.txt"}]
   ```

3. `model-pack TYPE MANIFEST.json` returns two models and randomized case codes. Use identical assignments and low effort for both. Send only the input, task and `eval:CODE:SHA256` marker in each first message. For example, ask workers to check whether source material meets the criteria. Use distinct authorized chats with equivalent access and no history of the earlier decision. Save their actual outputs. Include all rework through acceptance in a continuous turn range.
4. An independent reviewer first scores each output blindly against the original criteria, then associates it with the model. In the results JSON the top key is the exact model name and the next is the case code. For each pair record the following.

   ```json
   {"input_sha256":"…","output":"evaluation/answer-a.txt","output_sha256":"…","decision":"changes","quality_pass":true,"thread":"CHAT_ID","first_turn":"FIRST_TURN","last_turn":"LAST_TURN"}
   ```

   `decision` is the judgement on the source revision (`approve` or `changes`). `quality_pass` covers completeness and correctness of the actual work, including cause when required. Time and tokens come from local Codex logs. Count elapsed time from the first request to completion of the last turn, and input and output for the whole range.

5. The reviewer runs `model-score PACK_ID RESULTS.json`. Both models must complete the full set and match the independent judgement on every case. To qualify, the fast model must also reduce total time and stay within the default model's total tokens. Incomplete telemetry, wrong models, changed files or reused turns abort scoring. `model-status` shows project decisions. After a regression use a fresh pack including that case.
