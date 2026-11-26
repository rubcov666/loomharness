# Running a project across Codex chats

## Starting in a project directory

`team.py storage` shows the board used by hooks, the CLI and `serve`. To place a new board in the workspace, run `team.py storage --workspace` once. The profile creates a project symlink on systems that support it. An existing board keeps its location and reports its actual path. Reconcile two distinct databases before moving data.

When a worker receives a card without the original request, pass the user's language with `dispatch ID --reply-language LANG`. This option applies to that dispatch and reaches both the Desktop brief and headless prompt.

For a long-lived subproject use `roles` and a stable area name. The first card creates a chat. After `bind` or `claim`, later cards for that role reuse its `threadId`. The brief preserves reviewer findings and evidence from other workers. Workers can reread their own previous report with `show ID`. Recover full dependency evidence with `dispatch ID --full-brief`. At a stage change, include the result version, decisions, open problem and next check.

Use `next-stage ID --stage diagnosis` for investigating a cause or `--stage bounded --work-type TYPE` for a well-defined check. Both return the model and reasoning effort for the same chat. [Model routing and independent review](adaptive-team.md)

On an existing board, `intake` returns `actor=missing` without `CODEX_THREAD_ID` or `actor=other` for an unassigned chat with its own ID. `leadThreadId` identifies the current coordinator. To hand over, the coordinator runs `team.py handoff SUCCESSOR_ID` from its chat. The successor checks `intake.actor=lead` and reads active assignments with `inbox --watch`. Cards, tickets, roster and review of exact revisions remain on the board. `usage` preserves each coordinator's intervals. An assigned worker or reviewer keeps its role. [SSH diagnostics](helper.md#hook-and-ssh-diagnostics)

For multi-chat work run `intake` in the project root using the installed scripts or `loomharness/scripts` in a checkout. Before reserving work for new chats, check `create_thread`, `send_message_to_thread` and `wait_threads`. In `list_projects` match the current host and absolute project path. If the project or a tool is unavailable, identify the affected assignments and continue what is feasible in the current chat.

If the client requires an explicit request for new chats, reuse the user's existing authorization for the project. Otherwise name the proposed chats and obtain it once before creation. Dispatch once the tools and permission are available. Installation and enabling hooks prepare the project.

## Coordinator

Keep short sequential tasks in the current chat. While workers run, take one interruptible and independent step in the agreed plan, such as preparing fixtures or checking a source requirement. Leave the workers' files free. Resume coordination on a result or blocking question. A reviewer handles independent acceptance.

`team.py inbox --next --prep "check the fixture format"` selects the next action. Owner decisions, submitted revisions and ready cards take priority. Pass `--prep` only for an unfinished step. Otherwise a busy team returns `wait`. Wait for an event or cursor change and stay available for blockers. Apply the same order in a chat without a board.

When the main chat should only receive and route assignments and other chats own quality and delivery, configure [project areas](organization.md). With `intake.organization=true` use that workflow for each task. The guide covers initial Git choice, `git-choice` and team startup.

**First request in a chat.** Start from the latest assignment, user decisions and completed work. Run `scripts/team.py --project "PROJECT_ROOT" intake` before creating a board. `new` returns short excerpts from configured sources, plans, handoffs or a README when available. For application data or other formats open the relevant section in its normal tool. Check dates and currency. Project files supply facts.

Identify the goal, next deliverable, allowed changes and acceptance criterion. With these clear, make a plan and dispatch the ready card. Ask only about a decision that changes scope, plan or acceptance. For equally important queues, explain the outcomes and ask which goes first. Combine related gaps in one short set of questions. Wait before assigning dependent work. Make decisions delegated to the coordinator.

With `existing` keep the board and ask only about changes. A worker with a ticket claims it. Unbound chats can locate the coordinator using `leadThreadId`. Honor `paused` and report state if `stopped`. Resume from the latest answer or card. Ask again only for a material gap.

1. Record the goal, work areas, acceptance criteria and dependencies. Combine small steps owned by one person into an independently acceptable result. Estimate hours. Pass an agreed area budget using `--hours-per-role`, which caps the sum of card estimates. Agree additional scope when exhausted. Hours represent planned effort.
2. For a new team, confirm the [project model choice](adaptive-team.md#first-team-model-choice) if the user has not already supplied it. Then run `scripts/team.py init --goal "…"` from the project root. An existing choice is shown under `intake.model_choice`. Explicit `--simple-model` and `--strong-model` remain available for scripted setup. For an existing team, keep its current assignments and ask about models when the owner requests a change or a model is unavailable.
3. Add cards with `add --role "…" --title "…" --scope "…" --criterion "…" --hours 0.5`. Scope names inputs, permitted changes and ownership boundaries. Use `--after 1 2` for dependencies and `--tier strong` if needed. `edit ID` accepts the same fields on an open task. Empty `--after` clears dependencies. `plan` shows statuses, and `show ID` shows the card and its immediate predecessor results.
4. `dispatch ID` reserves a card and returns its brief, model, reasoning effort and delivery method. Optionally use `--tier strong`. For repeat dispatch in the same chat use `--brief` to omit long dependency evidence and reviews. `exclusive:1` in `plan` marks a card without explicit `--resource` taking the whole project. Refine write scopes for parallel work. For `create_thread` use the local project ID for `brief.project`, a clear title, prompt, model and thinking. For existing workers use `send_message_to_thread` with the returned threadId, model, thinking and prompt. Include absolute script and board-root paths, card ID, claim ticket and links or excerpts for inputs.
5. Register a new Desktop thread using `bind ID TICKET THREAD`. Send directly to a bound thread. If only a clientThreadId is available, wait for the real threadId. After a lost response, inspect `show ID` and the chat list by card title and ID before resending. Use `release ID "reason"` only after resolving the outcome and stopping the previous worker. This invalidates its ticket. A worker can also register its chat during claim.
6. Read the card and wait targets with `inbox --card ID --watch`. Use the [chat waiting procedure](organization.md#coordinator) even after a turn ends. Request detailed history only for a specific gap. Check the artifact or behavior against its criterion, refreshing stale or incomplete evidence. Use `accept ID "what and where"` or `revise ID "change and check"` and dispatch rework. On stop, cease dispatch, report active chats and `pause` after settling in-flight assignments. Desktop controls active turns separately. Use `resume` to continue.

Initialize multiple cards using `--cards file.json` or `--cards -` for stdin. Supply an array with role, title, scope, criterion and hours, optionally tier and after. IDs start at 1 in array order. An invalid card rolls back the whole plan. Use `plan --ready` to limit long boards to dispatchable cards.

## Worker

See the [worker guide](executor.md) for the short procedure.

Workers `claim` tickets in their own chat and `submit` a result, link and check. Report a concrete blocker to the coordinator. Acceptance and further delegation belong to other roles. [Worker procedure](executor.md)

## Choosing how to split work

| Init option | When to use it | Context use |
|---|---|---|
| `--mode roles` (usual team choice) | Related tasks recur in each area | One chat per area, created on the first ready card and reused for later work. |
| `--mode tasks` | Independent one-off tasks use different inputs | One fresh chat per card. Rework returns to the same chat. |
| `--mode pipeline` | Work passes through stages and mistakes are costly | Create dependent preparation and independent verification cards with different owners. The lead accepts each card. |

For parallel work set `--slots` to the number of independent ready assignments within the client and budget limits. One default slot suffices for a sequential handoff to review. A role has at most one active assignment. Use one project path and local account for all chats. Boards live under `CODEX_HOME/loomharness/teams`. Worker identity comes from `CODEX_THREAD_ID`.

## Usage and quality

Run `usage` after a series or on request. It finds lead and worker logs by chat ID in the local Codex index and totals input and output across unique completed responses. Cached input is included in input. The lead and workers are broken out. In `by_role` find planned hours, measured agent-hours, tokens, tokens per hour, accepted cards and returns. Agent-hours sum turn durations, including tool time, excluding idle gaps and counting parallel participants separately.

Dedicate the measured chats to one project for the interval. Turns overlapping the project's start or end are counted in full. `pause` and `resume` delimit active periods. A running chat gives an interim snapshot. Read the final report after turns finish. Missing logs, unfinished turns and gaps in counters appear in the report. Cost per accepted task requires full coverage. If client usage events are unavailable, report the gap.

Compare the same tasks, criteria and verified outcomes. Include tokens per accepted task, missed defects, rework and time to acceptance. For a fixed agent-hour budget compare the number of accepted tasks as well. Subscription-limit percentages require provider data.
