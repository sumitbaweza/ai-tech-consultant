// The inbox-worker service (`pnpm worker`): syncs every Gmail mailbox once a minute.
// A plain loop, like the Brain's worker: no scheduler library to explain.
import { setTimeout as sleep } from "node:timers/promises";
import { connect } from "@/db/client";
import { syncAllMailboxes } from "@/lib/gmail/sync";
import { loadRootEnv } from "@/root-env";

loadRootEnv();

const INTERVAL_MS = 60_000;
const db = connect();

// `docker compose stop` sends SIGTERM and waits 10 seconds. Aborting cuts the minute's
// sleep short, so the loop ends cleanly instead of being killed mid-write.
const stop = new AbortController();
for (const signal of ["SIGTERM", "SIGINT"] as const) {
  process.once(signal, () => {
    stop.abort();
  });
}

console.log("Inbox worker: syncing Gmail mailboxes every minute");
while (!stop.signal.aborted) {
  try {
    for (const { address, outcome } of await syncAllMailboxes(db)) {
      if (!outcome.ok) {
        console.error(`${address}: ${outcome.error}`);
      } else if (outcome.result.kind === "full" || outcome.result.saved + outcome.result.deleted > 0) {
        const { kind, saved, deleted } = outcome.result;
        console.log(`${address}: ${kind} sync, ${String(saved)} saved, ${String(deleted)} deleted`);
      }
    }
  } catch (error) {
    // The database is unreachable, say: try again next minute rather than exit
    console.error("Sync round failed", error);
  }
  await sleep(INTERVAL_MS, undefined, { signal: stop.signal }).catch(() => undefined);
}

await db.$client.end();
console.log("Inbox worker stopped");
