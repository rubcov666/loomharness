# Delivering a result

[Independent acceptance](review.md) · [Coordinator procedure](organization.md)

After acceptance the coordinator assigns one delivery chat with `delivery ID [ID ...] --revision HASH --target "exact destination" --action prepare`. It can prepare a package and instructions. For actual delivery use `--action publish --effects handoff --authority "owner request"`. Include `git` or `service` effects and the exact target when separately authorized. Choosing Git at setup does not itself authorize publication. Follow the applicable access and confirmation rules.

Package the agreed revision. For accepted changes with different hashes, create a dependent integration card, verify the combined build or archive and deliver that revision. The release queue is sequential. The worker uses `job-claim` and checks the actual destination, revision, previous attempt and recovery path for a changing service. Verify the result at the destination, such as downloaded bytes and hash, recorded version or application availability. Record `delivered JOB TICKET "what and where verified" --revision HASH`.

If the external outcome is unknown, keep the job active, inspect the destination and tell the coordinator. Inspect it with `job JOB`. Once cancellation is confirmed the coordinator can run `job-cancel JOB "verified outcome"`. Access grants, hosting rules and service permissions are enforced by the executing tool.
