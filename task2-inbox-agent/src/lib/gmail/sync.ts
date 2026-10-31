// Copies a Gmail account into inbox.emails: everything from the last 30 days the first time,
// then only what changed. Every write is an upsert or a delete, so a sync that runs twice,
// or twice at once (the worker and the Sync now button), leaves the same rows.
import { and, eq, inArray, isNotNull, sql } from "drizzle-orm";
import type { Db } from "@/db/client";
import { emails, mailboxes } from "@/db/schema";
import { getEnv } from "@/env";
import { type GmailApi, type GmailSender, gmailForMailbox, isRevokedSignIn } from "@/lib/gmail/client";
import { type ParsedMessage, parseMessage } from "@/lib/gmail/parse";

export const FIRST_SYNC_QUERY = "newer_than:30d";
export const FIRST_SYNC_MAX = 500;
// Messages fetched in parallel: well inside Gmail's per-user quota (messages.get costs 5 of
// the 250 units a second)
const FETCH_AT_ONCE = 10;

export type SyncResult = { kind: "full" | "changes"; saved: number; deleted: number };

/** Insert new emails; for ones already stored, update only what Gmail lets change on a
 *  message: its folder and read state. (Editing a Gmail draft makes a new message.) */
export async function upsertEmails(db: Db, mailboxId: number, rows: ParsedMessage[]): Promise<void> {
  if (rows.length === 0) return;
  await db
    .insert(emails)
    .values(rows.map((row) => ({ ...row, mailboxId })))
    .onConflictDoUpdate({
      target: [emails.mailboxId, emails.sourceId],
      set: { folder: sql`excluded.folder`, isRead: sql`excluded.is_read` },
    });
}

async function saveMessages(db: Db, mailboxId: number, api: GmailApi, ids: string[]): Promise<number> {
  let saved = 0;
  for (let start = 0; start < ids.length; start += FETCH_AT_ONCE) {
    const batch = ids.slice(start, start + FETCH_AT_ONCE);
    const messages = await Promise.all(batch.map((id) => api.getMessage(id)));
    const rows = messages.flatMap((message) => {
      const parsed = message === undefined ? undefined : parseMessage(message);
      return parsed === undefined ? [] : [parsed];
    });
    await upsertEmails(db, mailboxId, rows);
    saved += rows.length;
  }
  return saved;
}

async function deleteEmails(db: Db, mailboxId: number, ids: string[]): Promise<number> {
  if (ids.length === 0) return 0;
  const deleted = await db
    .delete(emails)
    .where(and(eq(emails.mailboxId, mailboxId), inArray(emails.sourceId, ids)))
    .returning({ id: emails.id });
  return deleted.length;
}

/** One sync of one mailbox through `api`, real or fake. */
export async function syncMailbox(
  db: Db,
  mailbox: { id: number; historyId: string | null },
  api: GmailApi,
): Promise<SyncResult> {
  if (mailbox.historyId !== null) {
    const changes = await api.listChanges(mailbox.historyId);
    if (changes !== "expired") {
      const saved = await saveMessages(db, mailbox.id, api, changes.changedIds);
      const deleted = await deleteEmails(db, mailbox.id, changes.deletedIds);
      await db.update(mailboxes).set({ historyId: changes.historyId }).where(eq(mailboxes.id, mailbox.id));
      return { kind: "changes", saved, deleted };
    }
  }

  // Full sync. Note the change log's position first: whatever changes while the list below
  // runs is then picked up by the next sync instead of being missed.
  const historyId = await api.currentHistoryId();
  const ids = await api.listMessageIds(FIRST_SYNC_QUERY, FIRST_SYNC_MAX);
  const saved = await saveMessages(db, mailbox.id, api, ids);
  await db.update(mailboxes).set({ historyId }).where(eq(mailboxes.id, mailbox.id));
  return { kind: "full", saved, deleted: 0 };
}

type MakeApi = (db: Db, mailbox: typeof mailboxes.$inferSelect) => GmailApi;

/** The real Gmail client for a mailbox, from its saved sign-in. */
export function realGmail(db: Db, mailbox: typeof mailboxes.$inferSelect): GmailApi & GmailSender {
  const env = getEnv();
  if (env.GOOGLE_CLIENT_ID === undefined || env.GOOGLE_CLIENT_SECRET === undefined) {
    throw new Error("Google sign-in is not configured: set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET");
  }
  if (mailbox.refreshToken === null) throw new Error("No saved Google sign-in: sign in with Google again");
  return gmailForMailbox(
    db,
    { ...mailbox, refreshToken: mailbox.refreshToken },
    { clientId: env.GOOGLE_CLIENT_ID, clientSecret: env.GOOGLE_CLIENT_SECRET },
  );
}

export type MailboxSyncOutcome =
  | { ok: true; result: SyncResult }
  | { ok: false; error: string; needsSignIn: boolean };

/** Sync one mailbox and record how it went on its row, which the screens show. Never throws,
 *  so one broken mailbox can't stop the worker. */
export async function runMailboxSync(
  db: Db,
  mailboxId: number,
  makeApi: MakeApi = realGmail,
): Promise<MailboxSyncOutcome> {
  const [mailbox] = await db.select().from(mailboxes).where(eq(mailboxes.id, mailboxId));
  if (mailbox === undefined) return { ok: false, error: "No such mailbox", needsSignIn: false };

  try {
    const result = await syncMailbox(db, mailbox, makeApi(db, mailbox));
    await db
      .update(mailboxes)
      .set({ lastSyncedAt: sql`now()`, lastSyncError: null })
      .where(eq(mailboxes.id, mailbox.id));
    return { ok: true, result };
  } catch (error) {
    const needsSignIn = isRevokedSignIn(error);
    const message = needsSignIn
      ? "Google no longer accepts the saved sign-in. Sign out, then sign in with Google again."
      : error instanceof Error
        ? error.message
        : String(error);
    await db
      .update(mailboxes)
      .set({ lastSyncError: message, needsSignIn })
      .where(eq(mailboxes.id, mailbox.id));
    return { ok: false, error: message, needsSignIn };
  }
}

/** Every Gmail mailbox that has a working sign-in, one after another. */
export async function syncAllMailboxes(db: Db, makeApi: MakeApi = realGmail) {
  const due = await db
    .select({ id: mailboxes.id, address: mailboxes.address })
    .from(mailboxes)
    .where(
      and(
        eq(mailboxes.provider, "google"),
        isNotNull(mailboxes.refreshToken),
        eq(mailboxes.needsSignIn, false),
      ),
    );
  const outcomes: { address: string; outcome: MailboxSyncOutcome }[] = [];
  for (const mailbox of due) {
    outcomes.push({ address: mailbox.address, outcome: await runMailboxSync(db, mailbox.id, makeApi) });
  }
  return outcomes;
}
