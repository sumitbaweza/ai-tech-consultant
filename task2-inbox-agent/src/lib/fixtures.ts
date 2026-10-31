// Fixtures mode: emails from fixtures/emails.json instead of Gmail, so evals, tests and
// reviewers without access to the demo Gmail account all see the same inbox.
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { sql } from "drizzle-orm";
import { z } from "zod";
import type { Db } from "@/db/client";
import { emails, mailboxes } from "@/db/schema";
import { FOLDERS } from "@/lib/folders";

const FixtureEmailSchema = z.object({
  // Stable ids ("fx-001"), so evals can name a case and a second import skips it
  id: z.string().regex(/^fx-\d{3}$/),
  // Emails in one conversation share a thread id; a lone email is its own thread
  threadId: z.string().optional(),
  folder: z.enum(FOLDERS),
  from: z.object({ name: z.string(), address: z.email() }),
  to: z.array(z.email()).min(1),
  // Empty subjects and bodies are allowed on purpose: they are test cases
  subject: z.string(),
  body: z.string(),
  receivedAt: z.iso.datetime({ offset: true }),
  isRead: z.boolean().default(false),
});

export const FixturesFileSchema = z.object({
  // The address the fixture emails were sent to: the demo support inbox
  mailbox: z.email(),
  emails: z.array(FixtureEmailSchema).refine(
    (list) => new Set(list.map((email) => email.id)).size === list.length,
    "Every fixture email needs its own id",
  ),
});

export type FixturesFile = z.infer<typeof FixturesFileSchema>;

export const FIXTURES_PATH = join(process.cwd(), "fixtures", "emails.json");

export async function loadFixtures(path = FIXTURES_PATH): Promise<FixturesFile> {
  const text = await readFile(path, "utf8");
  return FixturesFileSchema.parse(JSON.parse(text));
}

/** Copy the fixtures into the database. Emails already there are left alone, so running it
 *  twice changes nothing, and what a reviewer did to them (read, moved) survives a restart. */
export async function importFixtures(
  db: Db,
  file: FixturesFile,
): Promise<{ mailboxId: number; total: number; inserted: number }> {
  return db.transaction(async (tx) => {
    const [mailbox] = await tx
      .insert(mailboxes)
      .values({ address: file.mailbox, provider: "fixtures" })
      .onConflictDoUpdate({ target: mailboxes.address, set: { updatedAt: sql`now()` } })
      .returning({ id: mailboxes.id });
    if (mailbox === undefined) throw new Error("The fixtures mailbox was not saved");

    if (file.emails.length === 0) return { mailboxId: mailbox.id, total: 0, inserted: 0 };

    const inserted = await tx
      .insert(emails)
      .values(
        file.emails.map((email) => ({
          mailboxId: mailbox.id,
          sourceId: email.id,
          threadId: email.threadId ?? email.id,
          folder: email.folder,
          fromName: email.from.name,
          fromAddress: email.from.address,
          toAddresses: email.to,
          subject: email.subject,
          bodyText: email.body,
          receivedAt: new Date(email.receivedAt),
          isRead: email.isRead,
          // A stable Message-ID, so replies thread the same way Gmail ones do
          messageIdHeader: `<${email.id}@fixtures.directory-demo.test>`,
        })),
      )
      .onConflictDoNothing({ target: [emails.mailboxId, emails.sourceId] })
      .returning({ id: emails.id });

    return { mailboxId: mailbox.id, total: file.emails.length, inserted: inserted.length };
  });
}
