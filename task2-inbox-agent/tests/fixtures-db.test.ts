// Runs against the consultant_test database as the inbox role. Skipped when Postgres isn't
// up (`docker compose up -d postgres`), so the other tests still run anywhere.
import { eq, sql } from "drizzle-orm";
import { afterAll, describe, expect, it } from "vitest";
import { connect } from "@/db/client";
import { runMigrations } from "@/db/migrate";
import { emails, mailboxes } from "@/db/schema";
import { importFixtures, loadFixtures } from "@/lib/fixtures";
import { listEmails, unreadCounts } from "@/lib/mailbox";

const db = process.env.INBOX_DB_PASSWORD ? connect("consultant_test") : undefined;
const reachable = db ? await db.execute(sql`select 1`).then(() => true, () => false) : false;

describe.skipIf(!reachable)("importFixtures", () => {
  const testDb = db as NonNullable<typeof db>;
  const address = "fixtures-test@inbox.test";

  afterAll(async () => {
    // Deleting the mailbox deletes its emails too (on delete cascade)
    await testDb.delete(mailboxes).where(eq(mailboxes.address, address));
    await testDb.$client.end();
  });

  it("loads every email once, however often it runs", async () => {
    await runMigrations(testDb);
    // A second run finds nothing left to apply
    expect(await runMigrations(testDb)).toBe(0);
    const file = { ...(await loadFixtures()), mailbox: address };

    const first = await importFixtures(testDb, file);
    const second = await importFixtures(testDb, file);

    expect(first.inserted).toBe(file.emails.length);
    expect(second.inserted).toBe(0);
    expect(second.mailboxId).toBe(first.mailboxId);
    const [row] = await testDb
      .select({ total: sql<number>`count(*)::int` })
      .from(emails)
      .where(eq(emails.mailboxId, first.mailboxId));
    expect(row?.total).toBe(file.emails.length);
  });

  it("lists a folder newest first and counts unread emails", async () => {
    const [mailbox] = await testDb.select().from(mailboxes).where(eq(mailboxes.address, address));
    if (!mailbox) throw new Error("The first test should have created the mailbox");

    const inbox = await listEmails(testDb, mailbox.id, "inbox");
    const times = inbox.map((email) => email.receivedAt.getTime());
    expect(times).toEqual([...times].sort((a, b) => b - a));

    const unread = await unreadCounts(testDb, mailbox.id);
    expect(unread.inbox).toBe(inbox.filter((email) => !email.isRead).length);
    expect(unread.sent).toBe(0);
  });
});
