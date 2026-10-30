import { describe, expect, it } from "vitest";
import { FixturesFileSchema, loadFixtures } from "@/lib/fixtures";
import { FOLDERS } from "@/lib/folders";

const email = {
  id: "fx-001",
  folder: "inbox",
  from: { name: "A", address: "a@example.com" },
  to: ["support@example.com"],
  subject: "Hi",
  body: "Hello",
  receivedAt: "2026-10-30T08:00:00Z",
};

describe("fixtures/emails.json", () => {
  it("passes the schema and fills every folder", async () => {
    const file = await loadFixtures();
    const folders = new Set(file.emails.map((e) => e.folder));
    expect([...folders].sort()).toEqual([...FOLDERS].sort());
  });
});

describe("FixturesFileSchema", () => {
  it("defaults isRead to false", () => {
    const file = FixturesFileSchema.parse({ mailbox: "support@example.com", emails: [email] });
    expect(file.emails[0]?.isRead).toBe(false);
  });

  it("rejects two emails with the same id", () => {
    const result = FixturesFileSchema.safeParse({ mailbox: "support@example.com", emails: [email, email] });
    expect(result.success).toBe(false);
  });

  it("rejects a bad sender address, an unknown folder and a date without a time zone", () => {
    for (const bad of [
      { ...email, from: { name: "A", address: "not-an-address" } },
      { ...email, folder: "archive" },
      { ...email, receivedAt: "2026-10-30 08:00" },
    ]) {
      expect(FixturesFileSchema.safeParse({ mailbox: "support@example.com", emails: [bad] }).success).toBe(false);
    }
  });
});
