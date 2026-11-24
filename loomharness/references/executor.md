# Worker procedure

Quick reference for the assigned worker. [Team workflow](team.md) · [Roles and queue](organization.md)

1. Before making changes, run `team.py --project "PROJECT_ROOT" claim ID TICKET` from the assigned chat. If the claim is rejected, end the turn without editing.
2. Work only on the claimed card within its agreed scope. Resolve missing inputs or overlapping work through the coordinator.
3. Save the result in its agreed location and run `submit ID TICKET "result, link or path, check and outcome"`. Supply `--revision HASH` and, when needed, an immutable snapshot via `--artifact relative/path`.
4. For a blocker complete the available work and report the cause, what you checked and the decision needed to the coordinator. The coordinator asks the owner. Independent acceptance and further delegation belong to other roles.
