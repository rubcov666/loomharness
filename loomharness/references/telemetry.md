# Time-window measurements

Use `team.py usage --summary` for repeated usage checks. The time-window report keeps totals, participant count, boundaries and coverage gaps. Use full output to compare participants. The short board report also retains time to acceptance.

To measure a task, specify the root chat and a time zone, e.g. `team.py usage --thread ID --since 2026-01-01T10:00:00Z --until 2026-01-01T11:00:00Z`. This reads the local subagent index and agents' own usage records. Repeat `--thread` for independent team chats. Start is inclusive and end exclusive. The report includes model responses, cached and fresh input, output, participants and coverage gaps. Compare the same tasks and revisions. Board `team.py usage` reports dispatch-to-acceptance time under `dispatch_to_accept`.

Give `harness_metrics` a local JSON path, e.g. `{"path":"/project/samples.json","threshold":2000,"summary":true}`. `root` limits reads to that directory. Small series can be passed inline in `data`.

For coverage, empty windows, peak and alert counts, set `summary: true` in MCP or `--summary` on the CLI. `alert_count` and `outside_alert_count` count alerts inside and outside the windows. Default output includes individual observations.

For exact threshold, peak or missing intervals, use `scripts/metrics.py data.json --at-least 2000` or pass `-` for stdin. Input has `windows` with `start`, `end` and optional `expected` count, and `samples` with `at` and `value`. Use ISO 8601 with a time zone. Windows include their start and exclude their end. Example: `{"windows":[{"start":"2026-01-01T09:00:00Z","end":"2026-01-01T09:01:00Z","expected":1}],"samples":[{"at":"2026-01-01T09:00:00Z","value":2000}]}`.

Output includes sample counts per window, empty windows, peaks, values at or above the threshold and observations outside windows. An empty window means no observations. Check source data to explain a cause. Duplicate timestamps, unknown schemas and unexpected record counts produce errors. Projects can convert their exports to this format locally.
