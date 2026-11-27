# Independent acceptance

The coordinator reads `id, revision` pairs with `team.py inbox --reviews`. Use `--after CURSOR --wait SECONDS` for revisions, stop or pause. Issue `team.py review ID --expect-revision SHA` for the returned pair. For multiple cards repeat `--expect-revision` in ID order. On SHA changes read the new event. Find an issued job with `job ID`. The reviewer ends its turn on a verdict or blocker. The coordinator resumes it for a new job or resolved blocker.

[Coordinator procedure](organization.md) · [Delivery](delivery.md) · [Background review and model selection](adaptive-team.md)

For native teams, use `team.py native review-next` for ready submissions. Give checks of the same pinned revision to the same reviewer. Review blocking cards immediately. Record verdicts per card and use a follow-up with the same reviewer for a later related submission.

When a worker submits, use the freed slot for another independent card. Read ready actions from `inbox`. `review_ready` offers up to four submissions. Send a single ready item immediately. Batch short cards with `review ID1 ID2 …` and send one brief to the strong reviewer. Use a separate `review ID` for larger cards. Workers can continue during review. The review pool allows two jobs, so a second can use time while the first waits for a background test. Delivery stays sequential.

A reviewer runs `job-claim JOB TICKET` once, opens the exact revisions and checks the criterion and edge cases. The coordinator can bind the chat first with `job-bind JOB TICKET THREAD`. Return `verdict JOB TICKET approve "reason" --revision HASH` or `changes` with a specific fix. For a verdict within a batch add `--task ID`.

Send multiple decisions with `verdicts JOB TICKET file.json` or `-` for stdin. For example:

```json
[
  {"task":12,"revision":"<card 12 hash>","decision":"approve","note":"Total matches the source data"},
  {"task":15,"revision":"<card 15 hash>","decision":"changes","note":"An empty input must produce zero"}
]
```

Use the actual hashes from the brief. The command validates the whole submitted list before committing it. A partial batch can be completed later. An approval unblocks dependencies and authorizes delivery of that revision under the usual rules. Return rework to the same worker. If a response is lost, inspect `job JOB` and `items`. Repeating the identical verdict preserves the result. Investigate the blocker after a repeated return for the same reason.

`usage` reports measured card count and median seconds from first dispatch to approval in `dispatch_to_accept`, including waiting and rework. Timing starts on cards first issued after the feature was installed.
