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

## Qualifying models on historical tasks

1. Set a routine model candidate in `.codex/harness-models.json`, for example `{"routine":"model-name"}`. The default model is the project's `--simple-model`. Compare for a new work type or model change. Ordinary dispatch creates no evaluation chats.
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
