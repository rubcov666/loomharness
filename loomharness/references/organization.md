# Persistent project areas, independent review and releases

## Initial setup

Start from the project's goals, materials and tasks. Areas can include research, datasets, procurement, editing or an application component. Assign a stable owner to related recurring work. Create its chat on first dispatch. Complete a one-off task in the current chat or use an explicitly requested temporary helper.

At initial planning ask about Git only if the choice is unknown. Save `--git none` for work without a repository. For an existing checkout use `--git existing` and optionally `--git-dir`. Creating a repository requires its own authorization. Clarify the delivery destination when it affects the next release, such as a folder, data store or application environment.

The main chat runs `team.py intake` and, for a fresh board, `init --mode roles` with a goal and two available models. Configure it with `team.py organize owners.json --slots 2 --git none` using the user's actual Git choice. The JSON object maps area names to short ownership scopes. Use `organize -` for stdin. Update roles between assignments and keep existing area names. Two slots are a starting point for independent work. Review and delivery use separate slots.

## Coordinator

When the owner wants autonomous completion and proposed improvements, use the [project survey and stage selection](initiative.md). The board summarizes occupied slots, next assignments, review queue, service jobs and owner questions. `inbox --roster` has its own cursor. Use `pulse`, `plan` and `show ID` for stage details.

Use `team.py inbox --watch`. Add `--card ID` to read one card with the board in the same transaction. Keep the full result and cursor. Repeat with the same options and `--after CURSOR`. On `{"changed":false}` reuse the saved state. Request a fresh snapshot if the saved result is missing.

For long evidence or frequent neighboring updates, use `inbox --watch --card ID --parts`. The first response includes `board`, `card` and one cursor. Targets are under `board.watch`. On updates, replace each returned part in full, retain missing parts and store the new cursor. On `changed: false` keep the saved state. After a resume with a missing part, request both without `--after`.

Map `watch.targets` to Desktop `wait_threads` targets using their known host and latest Desktop cursor. Keep each `assignment` beside its sent target. Handle the first completion, error or input request. For large teams rotate groups within the tool limit and next deadline.

Preserve `brief.on_blocker` at dispatch. A worker needing inputs or resolution of conflicting scopes completes the available part and ends its turn with the coordinator. Read the result, check available sources, reconcile scopes and send the decision. Ask the owner from the coordinator chat for access, permission or choices only they can provide. The owner handles system approval prompts.

After handling an `idle` result with a `completed` turn, pass `--settled ASSIGNMENT TIME` to later `inbox --watch` calls. Use receipt time in Unix seconds and keep that original value until recheck. Repeat for multiple chats. Clear it and process events after new messages, errors, approval requests or resumes. A new ticket or stage requeues the chat. If the turn ended with work still active, check once for submission or a blocker.

`watch.recheck_at` is the next Unix-time recheck, at most 30 seconds after settlement. Bound other waits by it. If all active assignments are temporarily excluded, use the same `inbox` options with `--after CURSOR --wait 30`. This observes board changes, stop markers and expiry. Finish the loop when no work or decisions remain. Handle owner messages and requests for access as they arrive.

Process each Desktop result once. [scripts/record_wait.js](../scripts/record_wait.js) updates settlement after event delivery. Supply `reply`, `sent`, `settled` and receipt time in seconds. `sent` contains the selected `watch.targets`. `settled` belongs to this project and coordinator chat. Discard a stale wait result if a new assignment was sent meanwhile.

Keep Desktop cursors separate from the board cursor. Clear expired settlement marks and clear a chat's mark before its next dispatch.

1. Check the request, scope and criterion against the team roster. Assign one owner. Create separate dependent cards for independent parts. Clarify conflicting requirements with the owner. The coordinator manages the queue, while workers implement and reviewers accept.
2. In `add` or `edit` pass the exact `--role` name and repeat `--resource` for shared write scopes. Use one canonical key per shared file, service or dataset across cards. Resource keys compare exactly and do not imply directory ancestry. Omit keys on a new card with unknown overlap to reserve the whole project. Isolate outputs or use immutable versions for parallel work. Include shared build directories and caches.
3. Dispatch ready cards with `dispatch ID`. Use its operation, threadId, model, thinking and brief as described in team.md. Register new chats using `bind`. Existing owners receive further work in the same chat. Add incoming user work to the board and dispatch when capacity opens. Wait for the first active chat event or owner input using `wait_threads` with cursors. The board stores state and the active coordinator performs dispatch.
4. The worker `claim`s and submits with `submit ID TICKET "result and evidence" --revision HASH`. Use a full available Git commit ID or the SHA-256 of an immutable file or archive. Pass `--artifact relative/path` from the shared board root. The revision must contain the deliverable under review. For a service, retain a versioned export or verifiable record and describe how to match it to the live object.

For a bounded stage use `--stage bounded --work-type TYPE` when dispatching. The board selects a fast model only for work types qualified against the default. Use `diagnosis` for an uncertain cause and `next-stage` between stages on the same card. [Model comparison and background review](adaptive-team.md)

Read the next relevant procedure on demand. [Independent review](review.md) · [Release preparation and delivery](delivery.md) · [Worker quick reference](executor.md)
