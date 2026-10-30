// The sync against a fake Gmail, writing to the consultant_test database. Skipped when
// Postgres isn't up (`docker compose up -d postgres`).
import type { gmail_v1 } from "@googleapis/gmail";
import { eq, sql } from "drizzle-orm";
import { afterAll, beforeEach, describe, expect, it } from "vitest";
import { connect } from "@/db/client";
import { runMigrations } from "@/db/migrate";
import { emails, mailboxes } from "@/db/schema";
import type { GmailApi, HistoryChanges } from "@/lib/gmail/client";
import { runMailboxSync, syncAllMailboxes, syncMailbox } from "@/lib/gmail/sync";

const db = process.env.INBOX_DB_PASSWORD ? connect("consultant_test") : undefined;
const reachable = db ? await db.execute(sql`select 1`).then(() => true, () => false) : false;

function gmailMessage(id: string, labelIds: string[], subject = `Subject ${id}`): gmail_v1.Schema$Message {
  return {
    id,
    threadId: id,
    labelIds,
    internalDate: "1793349120000",
    payload: {
      mimeType: "text/plain",
      headers: [
        { name: "From", value: "Customer <customer@example.com>" },
        { name: "To", value: "support@demo.test" },
        { name: "Subject", value: subject },
      ],
      body: { data: Buffer.from(`Body of ${id}`).toString("base64url") },
    },
  };
}

/** A Gmail account in memory. `changes` is what the next listChanges call reports. */
function fakeGmail(messages: Map<string, gmail_v1.Schema$Message>) {
  const state: { historyId: string; changes: HistoryChanges | "expired"; fullListings: number } = {
    historyId: "100",
    changes: { changedIds: [], deletedIds: [], historyId: "100" },
    fullListings: 0,
  };
  const api: GmailApi = {
    currentHistoryId: () => Promise.resolve(state.historyId),
    listMessageIds: (_query, max) => {
      state.fullListings += 1;
      return Promise.resolve([...messages.keys()].slice(0, max));
    },
    getMessage: (id) => Promise.resolve(messages.get(id)),
    listChanges: () => Promise.resolve(state.changes),
  };
  return { api, state };
}

describe.skipIf(!reachable)("Gmail sync", () => {
  const testDb = db as NonNullable<typeof db>;
  const address = "gmail-sync-test@inbox.test";
  let mailboxId = 0;

  beforeEach(async () => {
    await runMigrations(testDb);
    await testDb.delete(mailboxes).where(eq(mailboxes.address, address));
    const [row] = await testDb
      .insert(mailboxes)
      .values({ address, provider: "google", refreshToken: "fake-refresh-token" })
      .returning({ id: mailboxes.id });
    mailboxId = row?.id ?? 0;
  });

  afterAll(async () => {
    await testDb.delete(mailboxes).where(eq(mailboxes.address, address));
    await testDb.$client.end();
  });

  async function stored() {
    const rows = await testDb
      .select({ sourceId: emails.sourceId, folder: emails.folder, isRead: emails.isRead })
      .from(emails)
      .where(eq(emails.mailboxId, mailboxId))
      .orderBy(emails.sourceId);
    return rows;
  }

  it("copies every message on the first sync and remembers where the change log stands", async () => {
    const { api } = fakeGmail(
      new Map([
        ["m1", gmailMessage("m1", ["INBOX", "UNREAD"])],
        ["m2", gmailMessage("m2", ["SENT"])],
        ["m3", gmailMessage("m3", ["SPAM"])],
      ]),
    );
    const result = await syncMailbox(testDb, { id: mailboxId, historyId: null }, api);

    expect(result).toEqual({ kind: "full", saved: 3, deleted: 0 });
    expect(await stored()).toEqual([
      { sourceId: "m1", folder: "inbox", isRead: false },
      { sourceId: "m2", folder: "sent", isRead: true },
      { sourceId: "m3", folder: "spam", isRead: true },
    ]);
    const [mailbox] = await testDb.select().from(mailboxes).where(eq(mailboxes.id, mailboxId));
    expect(mailbox?.historyId).toBe("100");
  });

  it("then applies only the changes: new, moved, read and deleted mail", async () => {
    const messages = new Map([
      ["m1", gmailMessage("m1", ["INBOX", "UNREAD"])],
      ["m2", gmailMessage("m2", ["INBOX", "UNREAD"])],
    ]);
    const { api, state } = fakeGmail(messages);
    await syncMailbox(testDb, { id: mailboxId, historyId: null }, api);

    // In Gmail: m1 is read and trashed, m2 is deleted for good, m4 arrives
    messages.set("m1", gmailMessage("m1", ["TRASH"]));
    messages.delete("m2");
    messages.set("m4", gmailMessage("m4", ["INBOX", "UNREAD"]));
    state.changes = { changedIds: ["m1", "m4"], deletedIds: ["m2"], historyId: "150" };

    const result = await syncMailbox(testDb, { id: mailboxId, historyId: "100" }, api);

    expect(result).toEqual({ kind: "changes", saved: 2, deleted: 1 });
    expect(await stored()).toEqual([
      { sourceId: "m1", folder: "trash", isRead: true },
      { sourceId: "m4", folder: "inbox", isRead: false },
    ]);
    expect(state.fullListings).toBe(1);
  });

  it("falls back to a full sync when Gmail's change log no longer reaches back", async () => {
    const { api, state } = fakeGmail(new Map([["m1", gmailMessage("m1", ["INBOX"])]]));
    state.changes = "expired";

    const result = await syncMailbox(testDb, { id: mailboxId, historyId: "1" }, api);

    expect(result.kind).toBe("full");
    expect(state.fullListings).toBe(1);
    expect(await stored()).toHaveLength(1);
  });

  it("marks a refused sign-in, and the worker skips that mailbox until the next sign-in", async () => {
    const refused: GmailApi = {
      currentHistoryId: () => Promise.reject(new Error("invalid_grant: Token has been expired or revoked.")),
      listMessageIds: () => Promise.resolve([]),
      getMessage: () => Promise.resolve(undefined),
      listChanges: () => Promise.resolve("expired"),
    };

    const outcome = await runMailboxSync(testDb, mailboxId, () => refused);

    expect(outcome).toMatchObject({ ok: false, needsSignIn: true });
    const [mailbox] = await testDb.select().from(mailboxes).where(eq(mailboxes.id, mailboxId));
    expect(mailbox?.needsSignIn).toBe(true);
    expect(mailbox?.lastSyncError).toContain("sign in with Google again");

    const outcomes = await syncAllMailboxes(testDb, () => refused);
    expect(outcomes.map((o) => o.address)).not.toContain(address);
  });

  it("records the time of a good sync and clears an old error", async () => {
    await testDb.update(mailboxes).set({ lastSyncError: "earlier failure" }).where(eq(mailboxes.id, mailboxId));
    const { api } = fakeGmail(new Map());

    const outcome = await runMailboxSync(testDb, mailboxId, () => api);

    expect(outcome).toEqual({ ok: true, result: { kind: "full", saved: 0, deleted: 0 } });
    const [mailbox] = await testDb.select().from(mailboxes).where(eq(mailboxes.id, mailboxId));
    expect(mailbox?.lastSyncError).toBeNull();
    expect(mailbox?.lastSyncedAt).toBeInstanceOf(Date);
  });
});
