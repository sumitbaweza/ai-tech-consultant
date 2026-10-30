// The inbox schema: owned by the `inbox` login role (db/init/01-schemas.sh), which can't read
// the Brain's tables. Drizzle-kit loads this file on its own, so imports here are relative.
import {
  boolean,
  index,
  integer,
  numeric,
  pgSchema,
  serial,
  text,
  timestamp,
  unique,
  varchar,
} from "drizzle-orm/pg-core";
import { FOLDERS } from "../lib/folders";

export const inbox = pgSchema("inbox");

export const folderEnum = inbox.enum("folder", FOLDERS);

/** A mailbox the app reads: the fixtures one, or a Gmail account someone signed in with. */
export const mailboxes = inbox.table("mailboxes", {
  id: serial("id").primaryKey(),
  address: text("address").notNull().unique(),
  provider: text("provider", { enum: ["fixtures", "google"] }).notNull(),
  // Google's tokens, saved at sign-in so the background sync can read Gmail; null for fixtures
  refreshToken: text("refresh_token"),
  accessToken: text("access_token"),
  accessTokenExpiresAt: timestamp("access_token_expires_at", { withTimezone: true }),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
});

/** One email in one folder, from Gmail or from fixtures/emails.json. */
export const emails = inbox.table(
  "emails",
  {
    id: serial("id").primaryKey(),
    mailboxId: integer("mailbox_id")
      .notNull()
      .references(() => mailboxes.id, { onDelete: "cascade" }),
    // Gmail's message id, or the fixture's id. With mailbox_id it makes a second import a no-op.
    sourceId: text("source_id").notNull(),
    threadId: text("thread_id").notNull(),
    folder: folderEnum("folder").notNull(),
    fromName: text("from_name").notNull(),
    fromAddress: text("from_address").notNull(),
    toAddresses: text("to_addresses").array().notNull(),
    subject: text("subject").notNull(),
    // Untrusted customer text: shown and quoted as data, never followed as instructions
    bodyText: text("body_text").notNull(),
    receivedAt: timestamp("received_at", { withTimezone: true }).notNull(),
    isRead: boolean("is_read").notNull().default(false),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (table) => [
    unique("emails_mailbox_source_key").on(table.mailboxId, table.sourceId),
    // The folder list: one mailbox, one folder, newest first
    index("emails_folder_list_idx").on(table.mailboxId, table.folder, table.receivedAt),
  ],
);

/** One row per Claude call: the ledger behind every cost and speed number. Same columns as
 *  the Brain's brain.llm_calls, so EVALS.md can compare the tasks side by side. */
export const llmCalls = inbox.table("llm_calls", {
  id: serial("id").primaryKey(),
  // The prompt file that ran, e.g. "classify", and its version
  prompt: varchar("prompt", { length: 100 }).notNull(),
  promptVersion: integer("prompt_version"),
  // The model that answered, which a refusal fallback can change from the one requested
  model: varchar("model", { length: 100 }).notNull(),
  inputTokens: integer("input_tokens").notNull().default(0),
  outputTokens: integer("output_tokens").notNull().default(0),
  cacheReadTokens: integer("cache_read_tokens").notNull().default(0),
  cacheWriteTokens: integer("cache_write_tokens").notNull().default(0),
  costUsd: numeric("cost_usd", { precision: 12, scale: 6 }).notNull(),
  latencyMs: integer("latency_ms").notNull(),
  isBatch: boolean("is_batch").notNull().default(false),
  success: boolean("success").notNull(),
  stopReason: varchar("stop_reason", { length: 30 }),
  error: text("error"),
  // Free-form label that groups calls, such as one eval run
  runTag: varchar("run_tag", { length: 100 }),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
});
