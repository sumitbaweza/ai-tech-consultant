// The inbox-worker service (`pnpm worker`). Once a minute:
//   1. sync every Gmail mailbox;
//   2. classify and draft new Gmail mail;
//   3. send the auto-sends whose undo window has passed;
//   4. learn rules from edited sends.
// A plain loop, like the Brain's worker: no scheduler library to explain.
import { setTimeout as sleep } from "node:timers/promises";
import { getBrain } from "@/lib/brain";
import { syncAllMailboxes } from "@/lib/gmail/sync";
import { learnPending } from "@/lib/learn";
import { type Claude, getClaude, LLMUnavailable } from "@/lib/llm/claude";
import { processNewGmail } from "@/lib/pipeline/process";
import { sendDueDrafts } from "@/lib/review";
import { deliverReply } from "@/lib/send";
import { loadRootEnv } from "@/root-env";
import { getDb } from "@/db/client";

loadRootEnv();

const INTERVAL_MS = 60_000;
const db = getDb();

// `docker compose stop` sends SIGTERM and waits 10 seconds. Aborting cuts the minute's
// sleep short, so the loop ends cleanly instead of being killed mid-write.
const stop = new AbortController();
for (const signal of ["SIGTERM", "SIGINT"] as const) {
  process.once(signal, () => {
    stop.abort();
  });
}

/** One step of the round. A failing step is logged and the round goes on. */
async function step(name: string, run: () => Promise<void>): Promise<void> {
  try {
    await run();
  } catch (error) {
    console.error(`${name} failed`, error);
  }
}

let warnedNoKey = false;

console.log("Inbox worker: syncing, drafting, sending and learning every minute");
while (!stop.signal.aborted) {
  await step("Gmail sync", async () => {
    for (const { address, outcome } of await syncAllMailboxes(db)) {
      if (!outcome.ok) console.error(`${address}: ${outcome.error}`);
      else if (outcome.result.kind === "full" || outcome.result.saved + outcome.result.deleted > 0) {
        const { kind, saved, deleted } = outcome.result;
        console.log(`${address}: ${kind} sync, ${String(saved)} saved, ${String(deleted)} deleted`);
      }
    }
  });

  await step("Auto-sends", async () => {
    const sent = await sendDueDrafts(db, deliverReply);
    if (sent > 0) console.log(`Auto-sent ${String(sent)} draft(s)`);
  });

  // Drafting and learning need Claude: without a key the rest of the inbox still works
  let claude: Claude | undefined;
  try {
    claude = getClaude();
  } catch (error) {
    if (!(error instanceof LLMUnavailable)) throw error;
    if (!warnedNoKey) console.warn(`No drafting or learning: ${error.message}`);
    warnedNoKey = true;
  }
  if (claude !== undefined) {
    const deps = { claude, brain: getBrain() };
    await step("Drafting new mail", async () => {
      await processNewGmail(db, deps);
    });
    await step("Learning", async () => {
      await learnPending(db, claude);
    });
  }

  await sleep(INTERVAL_MS, undefined, { signal: stop.signal }).catch(() => undefined);
}

await db.$client.end();
console.log("Inbox worker stopped");
