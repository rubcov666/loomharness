// Update settled markers after delivering wait_threads poll replies.
// Read this file into the coordinator's program loop instead of copying the
// function by hand; the copy in organization.md stays a plain pointer.
//
// reply: one wait_threads response. sent: records from watch.targets chosen for
// this wait. settled: Map assignment -> Unix seconds for the current project and
// chat. receivedAt: reply receipt time in whole seconds.
function recordWait(reply, sent, settled, receivedAt) {
  if (!Array.isArray(reply.polls) || Object.keys(reply.errors ?? {}).length ||
      reply.message || (reply.wake && reply.wake.reason !== "turnCompleted")) {
    settled.clear();
    return;
  }
  for (const poll of reply.polls ?? []) {
    const target = sent.find(item => item.threadId === poll.thread?.id);
    if (!target) continue;
    const status = poll.thread.status;
    if (status?.type === "idle" && !status.activeFlags?.length &&
        poll.latestTurn?.status === "completed" && poll.cursor) {
      settled.set(target.assignment, receivedAt);
    } else {
      settled.delete(target.assignment);
    }
  }
}
