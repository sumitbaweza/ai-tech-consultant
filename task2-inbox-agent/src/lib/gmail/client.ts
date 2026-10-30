// The four Gmail calls the sync needs, behind a small interface, so tests use a fake.
import { gmail, type gmail_v1 } from "@googleapis/gmail";
import { eq } from "drizzle-orm";
import { OAuth2Client } from "google-auth-library";
import type { Db } from "@/db/client";
import { mailboxes } from "@/db/schema";

export type HistoryChanges = {
  // Messages added, or whose labels changed (read, moved): fetch them again
  changedIds: string[];
  // Messages deleted for good: remove their rows
  deletedIds: string[];
  // Where the change log stands now; the next sync starts here
  historyId: string;
};

export type GmailApi = {
  /** Where the mailbox's change log stands right now. */
  currentHistoryId(): Promise<string>;
  /** Ids of messages matching a Gmail search, spam and trash included, newest first. */
  listMessageIds(query: string, max: number): Promise<string[]>;
  /** One message in full, or undefined if it was deleted in the meantime. */
  getMessage(id: string): Promise<gmail_v1.Schema$Message | undefined>;
  /** What changed since `startHistoryId`, or "expired" when Gmail no longer keeps that far
   *  back (about a week): then only a full sync can catch up. */
  listChanges(startHistoryId: string): Promise<HistoryChanges | "expired">;
};

/** The HTTP status of a failed Google call, if it has one. */
export function httpStatus(error: unknown): number | undefined {
  if (typeof error !== "object" || error === null) return undefined;
  if ("status" in error && typeof error.status === "number") return error.status;
  if ("code" in error && typeof error.code === "number") return error.code;
  return undefined;
}

/** True when Google refused the refresh token itself: revoked, or older than 7 days while the
 *  OAuth app is in Testing mode. Retrying can't help; the person has to sign in again. */
export function isRevokedSignIn(error: unknown): boolean {
  return error instanceof Error && error.message.includes("invalid_grant");
}

type MailboxTokens = {
  id: number;
  refreshToken: string;
  accessToken: string | null;
  accessTokenExpiresAt: Date | null;
};

export function gmailForMailbox(
  db: Db,
  mailbox: MailboxTokens,
  oauthClient: { clientId: string; clientSecret: string },
): GmailApi {
  const auth = new OAuth2Client(oauthClient);
  auth.setCredentials({
    refresh_token: mailbox.refreshToken,
    access_token: mailbox.accessToken,
    expiry_date: mailbox.accessTokenExpiresAt?.getTime() ?? null,
  });
  // The library swaps the refresh token for a new access token when the old one (1 hour)
  // runs out, and announces it here. Saving it spares the next run that round trip.
  auth.on("tokens", (tokens) => {
    if (!tokens.access_token) return;
    db.update(mailboxes)
      .set({
        accessToken: tokens.access_token,
        accessTokenExpiresAt: tokens.expiry_date ? new Date(tokens.expiry_date) : null,
      })
      .where(eq(mailboxes.id, mailbox.id))
      .catch((error: unknown) => {
        console.error("Could not save the new Google access token", error);
      });
  });
  const api = gmail({ version: "v1", auth });

  return {
    async currentHistoryId() {
      const profile = await api.users.getProfile({ userId: "me" });
      if (!profile.data.historyId) throw new Error("Gmail returned no historyId");
      return profile.data.historyId;
    },

    async listMessageIds(query, max) {
      const ids: string[] = [];
      let pageToken: string | undefined;
      do {
        const page = await api.users.messages.list({
          userId: "me",
          q: query,
          includeSpamTrash: true,
          maxResults: Math.min(100, max - ids.length),
          pageToken,
        });
        for (const item of page.data.messages ?? []) if (item.id) ids.push(item.id);
        pageToken = page.data.nextPageToken ?? undefined;
      } while (pageToken !== undefined && ids.length < max);
      return ids;
    },

    async getMessage(id) {
      try {
        const response = await api.users.messages.get({ userId: "me", id, format: "full" });
        return response.data;
      } catch (error) {
        if (httpStatus(error) === 404) return undefined;
        throw error;
      }
    },

    async listChanges(startHistoryId) {
      const changed = new Set<string>();
      const deleted = new Set<string>();
      let historyId = startHistoryId;
      let pageToken: string | undefined;
      try {
        do {
          const page = await api.users.history.list({ userId: "me", startHistoryId, pageToken });
          for (const record of page.data.history ?? []) {
            for (const item of [
              ...(record.messagesAdded ?? []),
              ...(record.labelsAdded ?? []),
              ...(record.labelsRemoved ?? []),
            ]) {
              if (item.message?.id) changed.add(item.message.id);
            }
            for (const item of record.messagesDeleted ?? []) {
              if (item.message?.id) deleted.add(item.message.id);
            }
          }
          historyId = page.data.historyId ?? historyId;
          pageToken = page.data.nextPageToken ?? undefined;
        } while (pageToken !== undefined);
      } catch (error) {
        if (httpStatus(error) === 404) return "expired";
        throw error;
      }
      // A message changed and then deleted is just deleted
      return {
        changedIds: [...changed].filter((id) => !deleted.has(id)),
        deletedIds: [...deleted],
        historyId,
      };
    },
  };
}
