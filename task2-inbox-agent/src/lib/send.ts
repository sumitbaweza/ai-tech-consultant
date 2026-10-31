// Delivering a reply. A Gmail mailbox sends through the Gmail API, in the customer's thread;
// the fixtures mailbox has nowhere to send to, so the reply is stored in its Sent folder.
import { eq, sql } from "drizzle-orm";
import type { Db } from "@/db/client";
import { emails, mailboxes } from "@/db/schema";
import type { GmailSender } from "@/lib/gmail/client";
import { realGmail } from "@/lib/gmail/sync";

export type Reply = {
  draftId: number;
  original: typeof emails.$inferSelect;
  subject: string;
  body: string;
};

/** Sends a reply; tests pass a fake. */
export type Deliver = (db: Db, reply: Reply) => Promise<void>;

/** A header value in RFC 2047 form, so subjects in any language survive mail servers. */
function encodeHeader(value: string): string {
  return /^[\x20-\x7e]*$/.test(value) ? value : `=?UTF-8?B?${Buffer.from(value, "utf8").toString("base64")}?=`;
}

/** The reply as an RFC 2822 message. In-Reply-To and References make every mail client put it
 *  in the customer's conversation. The body is base64, wrapped at 76 characters. */
export function buildReplyMime(message: {
  from: string;
  to: string;
  subject: string;
  body: string;
  inReplyTo: string | null;
}): string {
  const headers = [
    `From: ${message.from}`,
    `To: ${message.to}`,
    `Subject: ${encodeHeader(message.subject)}`,
    ...(message.inReplyTo === null ? [] : [`In-Reply-To: ${message.inReplyTo}`, `References: ${message.inReplyTo}`]),
    "MIME-Version: 1.0",
    'Content-Type: text/plain; charset="UTF-8"',
    "Content-Transfer-Encoding: base64",
  ];
  const body = Buffer.from(message.body.replace(/\r?\n/g, "\r\n"), "utf8").toString("base64");
  const wrapped = body.match(/.{1,76}/g)?.join("\r\n") ?? "";
  return `${headers.join("\r\n")}\r\n\r\n${wrapped}`;
}

export const deliverReply: Deliver = async (db, reply) => {
  const [mailbox] = await db.select().from(mailboxes).where(eq(mailboxes.id, reply.original.mailboxId));
  if (mailbox === undefined) throw new Error("The email's mailbox is gone");

  if (mailbox.provider === "fixtures") {
    // Nowhere to send to: keep the reply in Sent, in the same thread, like Gmail would
    await db.insert(emails).values({
      mailboxId: mailbox.id,
      sourceId: `reply-${String(reply.draftId)}`,
      threadId: reply.original.threadId,
      folder: "sent",
      fromName: "Support Team",
      fromAddress: mailbox.address,
      toAddresses: [reply.original.fromAddress],
      subject: reply.subject,
      bodyText: reply.body,
      receivedAt: new Date(),
      isRead: true,
    });
  } else {
    const gmail: GmailSender = realGmail(db, mailbox);
    const raw = buildReplyMime({
      from: mailbox.address,
      to: reply.original.fromAddress,
      subject: reply.subject,
      body: reply.body,
      inReplyTo: reply.original.messageIdHeader,
    });
    // The sent message comes back into Sent with the next sync
    await gmail.sendMessage(Buffer.from(raw, "utf8").toString("base64url"), reply.original.threadId);
  }
  await db.update(emails).set({ isRead: sql`true` }).where(eq(emails.id, reply.original.id));
};
