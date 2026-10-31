import { and, count, desc, eq, sql } from "drizzle-orm";
import type { Db } from "@/db/client";
import { analyses, drafts, emails, mailboxes } from "@/db/schema";
import type { Env } from "@/env";
import { FOLDERS, type Folder, isFolder, type MailList } from "@/lib/folders";

// What the screens need to know about a mailbox: never its tokens
const mailboxColumns = {
  id: mailboxes.id,
  address: mailboxes.address,
  provider: mailboxes.provider,
  lastSyncedAt: mailboxes.lastSyncedAt,
  lastSyncError: mailboxes.lastSyncError,
  needsSignIn: mailboxes.needsSignIn,
};

export type Mailbox = {
  id: number;
  address: string;
  provider: "fixtures" | "google";
  lastSyncedAt: Date | null;
  lastSyncError: string | null;
  needsSignIn: boolean;
};

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
    .select(mailboxColumns)
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
        // A fresh sign-in fixes a refused one
        needsSignIn: false,
        lastSyncError: null,
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
  // Archived mail has no place in the rail, so its count is left out
  for (const row of rows) if (isFolder(row.folder)) counts[row.folder] = row.unread;
  return counts;
}

export type EmailListRow = {
  id: number;
  fromName: string;
  subject: string;
  snippet: string;
  receivedAt: Date;
  isRead: boolean;
  intent: string | null;
  productArea: string | null;
  riskFlags: string[] | null;
  draftStatus: string | null;
};

// The newest draft's status for each email in the list, as a correlated subquery
const latestDraftStatus = sql<string | null>`(
  select ${drafts.status} from ${drafts} where ${drafts.emailId} = ${emails.id}
  order by ${drafts.id} desc limit 1
)`;

/** Emails waiting for a human: a draft to send, a failed send, an auto-send still in its undo
 *  window, or a sampled auto-send nobody has checked yet. */
const needsReview = sql`exists (
  select 1 from ${drafts} where ${drafts.emailId} = ${emails.id}
  and (${drafts.status} in ('ready', 'failed', 'scheduled')
       or (${drafts.status} = 'auto_sent' and ${drafts.sampledForReview} and ${drafts.reviewResult} is null))
)`;
const autoSent = sql`exists (
  select 1 from ${drafts} where ${drafts.emailId} = ${emails.id} and ${drafts.status} = 'auto_sent'
)`;

/** One folder's or view's emails, newest first, with the first 200 characters of the body,
 *  the category chips and the draft's state. */
export async function listEmails(db: Db, mailboxId: number, list: MailList, limit = 100): Promise<EmailListRow[]> {
  const where =
    list === "needs-review" ? needsReview : list === "auto-sent" ? autoSent : eq(emails.folder, list);
  return db
    .select({
      id: emails.id,
      fromName: emails.fromName,
      subject: emails.subject,
      snippet: sql<string>`left(${emails.bodyText}, 200)`,
      receivedAt: emails.receivedAt,
      isRead: emails.isRead,
      intent: analyses.intent,
      productArea: analyses.productArea,
      riskFlags: analyses.riskFlags,
      draftStatus: latestDraftStatus,
    })
    .from(emails)
    .leftJoin(analyses, eq(analyses.emailId, emails.id))
    .where(and(eq(emails.mailboxId, mailboxId), where))
    .orderBy(desc(emails.receivedAt))
    .limit(limit);
}

export async function countNeedsReview(db: Db, mailboxId: number): Promise<number> {
  const [row] = await db
    .select({ total: count() })
    .from(emails)
    .where(and(eq(emails.mailboxId, mailboxId), needsReview));
  return row?.total ?? 0;
}

/** One email of this mailbox (so URLs can't reach another mailbox's mail), with its analysis
 *  and newest draft. Views list mail from any folder, so only folders filter by folder. */
export async function getEmail(db: Db, mailboxId: number, list: MailList, id: number) {
  const conditions = [eq(emails.id, id), eq(emails.mailboxId, mailboxId)];
  if (isFolder(list)) conditions.push(eq(emails.folder, list));
  const [email] = await db.select().from(emails).where(and(...conditions)).limit(1);
  if (email === undefined) return undefined;
  const [analysis] = await db.select().from(analyses).where(eq(analyses.emailId, id));
  const [draft] = await db.select().from(drafts).where(eq(drafts.emailId, id)).orderBy(desc(drafts.id)).limit(1);
  return { email, analysis, draft };
}

/** Does this draft belong to an email of this mailbox? Every action on a draft checks it. */
export async function draftInMailbox(db: Db, draftId: number, mailboxId: number): Promise<boolean> {
  const [row] = await db
    .select({ id: drafts.id })
    .from(drafts)
    .innerJoin(emails, eq(emails.id, drafts.emailId))
    .where(and(eq(drafts.id, draftId), eq(emails.mailboxId, mailboxId)));
  return row !== undefined;
}
