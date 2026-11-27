---
name: loomharness
description: Manage multi-step work in CLI and MCP clients. Use for task intake, selective reads, verification, handoffs, and review.
---

# LoomHarness

Answer short questions directly. Write reusable prompts, hook guidance, task briefs and handoff instructions in English. Write user-facing messages in the language of the user's request unless they ask for another language. For code, a skill invocation, or a brief acknowledgment alone, keep the conversation language. Preserve identifiers and quoted text.

**Start.** Establish the goal, inputs, acceptance criteria, authorized external actions, and stopping conditions. Ask only what changes the decision. Resume from the saved plan and recent hook evidence. Record the version, date, and reason for plan changes. A chat note suffices for read-only work.

**Chats.** Handle short or sequential tasks here. For a new team use the [model questionnaire](references/adaptive-team.md#first-team-model-choice) with the host's interactive input when available. For delegated subprojects, reuse the [owner](references/team.md) and choose the model by stage. While workers run, take one interruptible, independent step already in the plan. Yield to submissions or blockers and wait for an event when no step qualifies. Wake the independent reviewer on the submitted revision. Reviewers finish idle turns. A [worker](references/executor.md) claims its ticket. Pass `dispatch --reply-language` when needed.

**Native teams.** When delegation is authorized and the host supports named agents and follow-up, use [`team.py native`](references/team.md#named-native-agents) for the board. Reuse one agent per role under its exact role name. Define any extra role from a real project assignment, for example `--agent-role designer`. Keep board areas as scope and use the assigned role for routing. Use compact status and group only already-ready related reviews.

**Read.** Fetch relevant fields, lines, headers, and exceptions at the source. Use `view.py --start N --lines K` or `rg -n -C 2` for text. Batch independent reads. Find tools by name, then fetch the selected schema. [Reports and versions](references/context.md) · [Code maps and command logs](references/commands.md) · [Telemetry windows](references/telemetry.md)

**Run.** Before reusing a result, check the goal, inputs, and settings. Use SHA-256 for one file, `context.py reuse` for several, and the object version for a service. After a write or launch times out, check its ID or history before retrying. Report an unknown outcome. Allow 30 seconds for short calls and their outer wait. Wait on background jobs by ID and handle failures or requests for input promptly.

**Long histories.** Link the full log. At the compaction threshold, weigh remaining calls (one for a final reply) against the cost of rereading details. Compact completed work when it pays off or space runs low. Retain exact requirements, exceptions, sources, and the latest complete turns. Check the size of the next input. [History budget](references/history.md)

**Verify.** Reproduce a fix before and after the change. Run required project checks and verify the result in the user's environment. Recheck discrepancies. Mark PASS when every criterion has fresh evidence. For a gap, report NEEDS_WORK and the missing evidence. [Evidence before state edits](references/helper.md) · [Plans and builds](references/verification.md) · [Independent review](references/review.md)

**Handoff.** In the summary, card, or HANDOFF.md, record the result path and revision, verification, next step, owner decisions, limits, and open risks. Provide a patch with context on request. Refresh changing facts on resume. For a retrospective, review actual uses and failures over the agreed period (default one quarter) and agree on rule changes with the owner.

**Authority.** Spawn a temporary subagent only on an explicit request in the current task. For a read-only review, one agent with the hook's model, `reasoning_effort=low`, and `fork_turns=none` usually suffices. Give it the goal, sources, and criteria. Verify its result. At AGENT_STOP, stop work and report status in chat. Merging, pushing directly to the main branch, and force-pushing require explicit consent.
