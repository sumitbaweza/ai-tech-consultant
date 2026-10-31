import type { gmail_v1 } from "@googleapis/gmail";
import { describe, expect, it } from "vitest";
import {
  extractBody,
  folderFromLabels,
  parseAddress,
  parseAddressList,
  parseMessage,
} from "@/lib/gmail/parse";

// Messages in the Gmail API's shape: headers as a list, bodies as base64url bytes
function part(mimeType: string, body: string | Buffer, extra: gmail_v1.Schema$MessagePart = {}) {
  const bytes = typeof body === "string" ? Buffer.from(body, "utf8") : body;
  return {
    mimeType,
    headers: [{ name: "Content-Type", value: `${mimeType}; charset="UTF-8"` }],
    body: { data: bytes.toString("base64url"), size: bytes.length },
    ...extra,
  };
}

function message(payload: gmail_v1.Schema$MessagePart, labelIds = ["INBOX", "UNREAD"]) {
  return {
    id: "18f0a1",
    threadId: "18f0a0",
    labelIds,
    internalDate: "1793349120000",
    payload: {
      ...payload,
      headers: [
        { name: "From", value: '"Lopez, Maria" <maria@example.com>' },
        { name: "To", value: "Support <support@demo.test>, sales@demo.test" },
        { name: "Subject", value: "Logo in the header" },
        { name: "Message-ID", value: "<CAF1234@mail.example.com>" },
        ...(payload.headers ?? []),
      ],
    },
  };
}

describe("folderFromLabels", () => {
  it("maps Gmail's system labels, with trash and spam first", () => {
    expect(folderFromLabels(["INBOX", "UNREAD"])).toBe("inbox");
    expect(folderFromLabels(["SENT"])).toBe("sent");
    expect(folderFromLabels(["DRAFT"])).toBe("drafts");
    expect(folderFromLabels(["SPAM", "INBOX"])).toBe("spam");
    expect(folderFromLabels(["TRASH", "SENT"])).toBe("trash");
    expect(folderFromLabels(["CATEGORY_UPDATES"])).toBe("archived");
  });
});

describe("parseAddress and parseAddressList", () => {
  it("reads names in quotes, plain names and bare addresses", () => {
    expect(parseAddress('"Lopez, Maria" <maria@example.com>')).toEqual({
      name: "Lopez, Maria",
      address: "maria@example.com",
    });
    expect(parseAddress("Tom Reilly <tom@example.net>")).toEqual({ name: "Tom Reilly", address: "tom@example.net" });
    expect(parseAddress("tom@example.net")).toEqual({ name: "tom@example.net", address: "tom@example.net" });
  });

  it("splits on commas outside quotes", () => {
    expect(parseAddressList('"Lopez, Maria" <maria@example.com>, b@example.com')).toEqual([
      "maria@example.com",
      "b@example.com",
    ]);
    expect(parseAddressList("")).toEqual([]);
  });
});

describe("extractBody", () => {
  it("prefers the plain part of a multipart/alternative email", () => {
    const body = extractBody({
      mimeType: "multipart/alternative",
      parts: [part("text/plain", "Plain words"), part("text/html", "<p>HTML words</p>")],
    });
    expect(body).toBe("Plain words");
  });

  it("turns HTML-only mail into text, without image or link targets", () => {
    const body = extractBody(
      part("text/html", '<p>Hello <a href="https://evil.example">there</a></p><img src="https://track.example/p.gif">'),
    );
    expect(body).toBe("Hello there");
  });

  it("skips attachments, even text ones", () => {
    const body = extractBody({
      mimeType: "multipart/mixed",
      parts: [part("text/plain", "notes.txt contents", { filename: "notes.txt" }), part("text/plain", "The real body")],
    });
    expect(body).toBe("The real body");
  });

  it("decodes the part's own charset, as older non-English mail uses", () => {
    const latin1 = part("text/plain", Buffer.from("Grüße aus München", "latin1"));
    latin1.headers = [{ name: "Content-Type", value: "text/plain; charset=ISO-8859-1" }];
    expect(extractBody(latin1)).toBe("Grüße aus München");
  });

  it("returns an empty string for an empty email", () => {
    expect(extractBody({ mimeType: "text/plain", body: { size: 0 } })).toBe("");
  });
});

describe("parseMessage", () => {
  it("fills every field of an emails row", () => {
    const parsed = parseMessage(message(part("text/plain", "Where do I change it?")));
    expect(parsed).toEqual({
      sourceId: "18f0a1",
      threadId: "18f0a0",
      folder: "inbox",
      fromName: "Lopez, Maria",
      fromAddress: "maria@example.com",
      toAddresses: ["support@demo.test", "sales@demo.test"],
      subject: "Logo in the header",
      bodyText: "Where do I change it?",
      receivedAt: new Date(1793349120000),
      isRead: false,
      messageIdHeader: "<CAF1234@mail.example.com>",
    });
  });

  it("marks mail without the UNREAD label as read", () => {
    expect(parseMessage(message(part("text/plain", "x"), ["SENT"]))?.isRead).toBe(true);
  });

  it("skips a message with no id or no payload", () => {
    expect(parseMessage({ payload: {} })).toBeUndefined();
    expect(parseMessage({ id: "1" })).toBeUndefined();
  });
});
