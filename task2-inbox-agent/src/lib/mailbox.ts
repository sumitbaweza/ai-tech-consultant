import { and, count, desc, eq, sql } from "drizzle-orm";
import type { Db } from "@/db/client";
import { emails, mailboxes } from "@/db/schema";
import type { Env } from "@/env";
import { FOLDERS, type Folder } from "@/lib/folders";

export type Mailbox = { id: number; address: string };

/** The mailbox the signed-in person sees: the fixtures one in fixtures mode, otherwise the
 *  Gmail account they signed in with. */
export async function currentMailbox(
  db: Db,
  env: Pick<Env, "INBOX_MODE">,
  signedInEmail: string | null | undefined,
): Promise<Mailbox | undefined> {
  const where =
    env.INBOX_MODE === "fixtures"
      ? eq(mailboxes.provider, "fixtures")
      : eq(mailboxes.address, signedInEmail ?? "");
  const [mailbox] = await db
    .select({ id: mailboxes.id, address: mailboxes.address })
    .from(mailboxes)
    .where(where)
    .limit(1);
  return mailbox;
}

/** Save Google's tokens at sign-in, so the background sync can read this Gmail account. */
export async function saveGoogleMailbox(
  db: Db,
  address: string,
  tokens: { refreshToken?: string; accessToken?: string; expiresAtSeconds?: number },
): Promise<void> {
  const expiresAt =
    tokens.expiresAtSeconds === undefined ? null : new Date(tokens.expiresAtSeconds * 1000);
  await db
    .insert(mailboxes)
    .values({
      address,
      provider: "google",
      refreshToken: tokens.refreshToken ?? null,
      accessToken: tokens.accessToken ?? null,
      accessTokenExpiresAt: expiresAt,
    })
    .onConflictDoUpdate({
      target: mailboxes.address,
      set: {
        // Google sends a refresh token only on consent; keep the old one when it's missing
        refreshToken: sql`coalesce(excluded.refresh_token, ${mailboxes.refreshToken})`,
        accessToken: sql`excluded.access_token`,
        accessTokenExpiresAt: sql`excluded.access_token_expires_at`,
        updatedAt: sql`now()`,
      },
    });
}

/** Unread emails per folder, for the counts in the left rail. */
export async function unreadCounts(db: Db, mailboxId: number): Promise<Record<Folder, number>> {
  const rows = await db
    .select({ folder: emails.folder, unread: count() })
    .from(emails)
    .where(and(eq(emails.mailboxId, mailboxId), eq(emails.isRead, false)))
    .groupBy(emails.folder);
  const counts = Object.fromEntries(FOLDERS.map((folder) => [folder, 0])) as Record<Folder, number>;
  for (const row of rows) counts[row.folder] = row.unread;
  return counts;
}

export type EmailListRow = {
  id: number;
  fromName: string;
  subject: string;
  snippet: string;
  receivedAt: Date;
  isRead: boolean;
};

/** One folder's emails, newest first, with the first 200 characters of the body. */
export async function listEmails(
  db: Db,
  mailboxId: number,
  folder: Folder,
  limit = 100,
): Promise<EmailListRow[]> {
  return db
    .select({
      id: emails.id,
      fromName: emails.fromName,
      subject: emails.subject,
      snippet: sql<string>`left(${emails.bodyText}, 200)`,
      receivedAt: emails.receivedAt,
      isRead: emails.isRead,
    })
    .from(emails)
    .where(and(eq(emails.mailboxId, mailboxId), eq(emails.folder, folder)))
    .orderBy(desc(emails.receivedAt))
    .limit(limit);
}

/** One email, only if it belongs to this mailbox and folder (so URLs can't reach others). */
export async function getEmail(db: Db, mailboxId: number, folder: Folder, id: number) {
  const [email] = await db
    .select()
    .from(emails)
    .where(and(eq(emails.id, id), eq(emails.mailboxId, mailboxId), eq(emails.folder, folder)))
    .limit(1);
  return email;
}
