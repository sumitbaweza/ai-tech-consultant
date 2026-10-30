// Turns one Gmail API message (format "full") into the fields of an `emails` row. Pure
// functions, no network, so every edge case has a test.
import type { gmail_v1 } from "@googleapis/gmail";
import { convert } from "html-to-text";
import type { StoredFolder } from "@/lib/folders";

type MessagePart = gmail_v1.Schema$MessagePart;

export type ParsedMessage = {
  sourceId: string;
  threadId: string;
  folder: StoredFolder;
  fromName: string;
  fromAddress: string;
  toAddresses: string[];
  subject: string;
  bodyText: string;
  receivedAt: Date;
  isRead: boolean;
};

/** Gmail labels -> our folder. A message can carry several (a sent reply in the inbox
 *  thread, a draft later trashed), so trash and spam win, as they do in Gmail's own view. */
export function folderFromLabels(labelIds: readonly string[]): StoredFolder {
  if (labelIds.includes("TRASH")) return "trash";
  if (labelIds.includes("SPAM")) return "spam";
  if (labelIds.includes("DRAFT")) return "drafts";
  if (labelIds.includes("SENT")) return "sent";
  if (labelIds.includes("INBOX")) return "inbox";
  return "archived";
}

/** `Maria Lopez <maria@example.com>`, `"Lopez, Maria" <maria@example.com>` or a bare address. */
export function parseAddress(value: string): { name: string; address: string } {
  const match = /^\s*(?:"?([^"]*?)"?\s*)?<([^>]+)>\s*$/.exec(value);
  if (match) {
    const address = (match[2] ?? "").trim();
    const name = (match[1] ?? "").trim();
    return { name: name === "" ? address : name, address };
  }
  const address = value.trim();
  return { name: address, address };
}

/** A To or Cc header -> addresses. Splits on commas, except inside quotes or <...>. */
export function parseAddressList(value: string): string[] {
  const parts: string[] = [];
  let current = "";
  let inQuotes = false;
  let inAngle = false;
  for (const char of value) {
    if (char === '"') inQuotes = !inQuotes;
    else if (char === "<") inAngle = true;
    else if (char === ">") inAngle = false;
    if (char === "," && !inQuotes && !inAngle) {
      parts.push(current);
      current = "";
    } else {
      current += char;
    }
  }
  parts.push(current);
  return parts.map((part) => parseAddress(part).address).filter((address) => address !== "");
}

function header(part: MessagePart | undefined, name: string): string | undefined {
  const lower = name.toLowerCase();
  return part?.headers?.find((h) => h.name?.toLowerCase() === lower)?.value ?? undefined;
}

/** Gmail sends body bytes as base64url, in the part's own character set (often UTF-8, but
 *  older mail clients use ISO-8859-1 or Windows-1252, common in non-English mail). */
function decodeBody(part: MessagePart): string {
  const data = part.body?.data;
  if (!data) return "";
  const bytes = Buffer.from(data, "base64url");
  const charset = /charset="?([^";\s]+)"?/i.exec(header(part, "Content-Type") ?? "")?.[1] ?? "utf-8";
  try {
    return new TextDecoder(charset).decode(bytes);
  } catch {
    // An unknown charset name: UTF-8 is the best guess
    return new TextDecoder("utf-8").decode(bytes);
  }
}

/** Every leaf part, depth first: multipart/alternative and multipart/mixed nest them. */
function leafParts(part: MessagePart): MessagePart[] {
  if (part.parts && part.parts.length > 0) return part.parts.flatMap(leafParts);
  return [part];
}

/** HTML -> readable text: links keep their words, images and styles go. The result is only
 *  ever shown as text, so no email HTML reaches the browser. */
export function htmlToText(html: string): string {
  return convert(html, {
    wordwrap: false,
    selectors: [
      { selector: "a", options: { ignoreHref: true } },
      { selector: "img", format: "skip" },
    ],
  }).trim();
}

/** The readable body: the first text/plain part that isn't an attachment, else the first
 *  HTML part converted to text, else nothing. */
export function extractBody(payload: MessagePart): string {
  const parts = leafParts(payload).filter((part) => !part.filename);
  const plain = parts.find((part) => part.mimeType === "text/plain");
  if (plain) return decodeBody(plain).trim();
  const html = parts.find((part) => part.mimeType === "text/html");
  if (html) return htmlToText(decodeBody(html));
  return "";
}

export function parseMessage(message: gmail_v1.Schema$Message): ParsedMessage | undefined {
  if (!message.id || !message.payload) return undefined;
  const labels = message.labelIds ?? [];
  const from = parseAddress(header(message.payload, "From") ?? "");
  return {
    sourceId: message.id,
    threadId: message.threadId ?? message.id,
    folder: folderFromLabels(labels),
    fromName: from.name,
    fromAddress: from.address,
    toAddresses: parseAddressList(header(message.payload, "To") ?? ""),
    subject: header(message.payload, "Subject") ?? "",
    bodyText: extractBody(message.payload),
    // When Gmail received it, in milliseconds. More reliable than the Date header, which
    // the sender's machine writes, and drafts may not have.
    receivedAt: new Date(Number(message.internalDate ?? "0")),
    isRead: !labels.includes("UNREAD"),
  };
}
