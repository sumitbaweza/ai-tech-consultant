// The inbox schema: owned by the `inbox` login role (db/init/01-schemas.sh), which can't read
// the Brain's tables. Drizzle-kit loads this file on its own, so imports here are relative.
import {
  boolean,
  index,
  integer,
  jsonb,
  numeric,
  pgSchema,
  real,
  serial,
  text,
  timestamp,
  unique,
  varchar,
} from "drizzle-orm/pg-core";
import {
  AUTONOMY_EVENTS,
  CATEGORY_STATES,
  DRAFT_STATUSES,
  type FactCheck,
  type PlaybookRule,
} from "../lib/domain";
import { STORED_FOLDERS } from "../lib/folders";

export const inbox = pgSchema("inbox");

export const folderEnum = inbox.enum("folder", STORED_FOLDERS);

/** A mailbox the app reads: the fixtures one, or a Gmail account someone signed in with. */
export const mailboxes = inbox.table("mailboxes", {
  id: serial("id").primaryKey(),
  address: text("address").notNull().unique(),
  provider: text("provider", { enum: ["fixtures", "google"] }).notNull(),
  // Google's tokens, saved at sign-in so the background sync can read Gmail; null for fixtures
  refreshToken: text("refresh_token"),
  accessToken: text("access_token"),
  accessTokenExpiresAt: timestamp("access_token_expires_at", { withTimezone: true }),
  // Gmail's position in the mailbox's change log after the last sync; the next sync asks
  // only for what changed since. Null until the first full sync.
  historyId: text("history_id"),
  lastSyncedAt: timestamp("last_synced_at", { withTimezone: true }),
  lastSyncError: text("last_sync_error"),
  // Google refused the refresh token (revoked, or 7 days old in Testing mode): sign in again
  needsSignIn: boolean("needs_sign_in").notNull().default(false),
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
    // The email's own Message-ID header: a reply quotes it so mail clients thread it
    messageIdHeader: text("message_id_header"),
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

// --- Classify, draft, learn, earn autonomy -------------------------------------------------

/** The configurable product areas emails are sorted into (test 2's list by default). */
export const productAreas = inbox.table("product_areas", {
  id: serial("id").primaryKey(),
  name: text("name").notNull().unique(),
  active: boolean("active").notNull().default(true),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
});

/** One row (id 1): the knobs on the Settings screen. */
export const settings = inbox.table("settings", {
  id: integer("id").primaryKey().default(1),
  // X: unedited sends in a row a category needs before it may auto-send
  autoSendAfter: integer("auto_send_after").notNull().default(20),
  // 1 in N auto-sent emails goes to a human for review
  sampleEvery: integer("sample_every").notNull().default(10),
  // How long an auto-send waits, so a human can still stop it
  undoMinutes: integer("undo_minutes").notNull().default(5),
  voiceGuide: text("voice_guide").notNull().default(""),
  updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
});

/** What the classifier and the guard found in one email. */
export const analyses = inbox.table("analyses", {
  emailId: integer("email_id")
    .primaryKey()
    .references(() => emails.id, { onDelete: "cascade" }),
  status: text("status", { enum: ["done", "failed"] }).notNull(),
  intent: text("intent"),
  productArea: text("product_area"),
  sentiment: text("sentiment"),
  urgency: text("urgency"),
  language: text("language"),
  riskFlags: text("risk_flags").array().notNull().default([]),
  guardFlags: text("guard_flags").array().notNull().default([]),
  // Standalone questions for the Brain, in English
  questions: text("questions").array().notNull().default([]),
  summary: text("summary"),
  error: text("error"),
  promptVersion: integer("prompt_version"),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
});

/** A drafted reply and what happened to it. The draft and the final text side by side are
 *  what the inbox learns from. */
export const drafts = inbox.table(
  "drafts",
  {
    id: serial("id").primaryKey(),
    emailId: integer("email_id")
      .notNull()
      .references(() => emails.id, { onDelete: "cascade" }),
    // "Same kind": intent and product area, e.g. "how_to:Header"
    categoryKey: text("category_key").notNull(),
    subject: text("subject").notNull(),
    body: text("body").notNull(),
    facts: jsonb("facts").$type<FactCheck[]>().notNull().default([]),
    brainStatus: text("brain_status", {
      enum: ["ok", "partial", "dont_know", "unavailable", "skipped"],
    }).notNull(),
    confidence: real("confidence"),
    openQuestions: text("open_questions").array().notNull().default([]),
    citedSourceIds: text("cited_source_ids").array().notNull().default([]),
    promptVersion: integer("prompt_version").notNull(),
    playbookVersion: integer("playbook_version").notNull().default(0),
    exampleDraftIds: integer("example_draft_ids").array().notNull().default([]),
    // Why this draft may never auto-send, whatever its category earned
    blockers: text("blockers").array().notNull().default([]),
    status: text("status", { enum: DRAFT_STATUSES }).notNull().default("ready"),
    scheduledFor: timestamp("scheduled_for", { withTimezone: true }),
    sentAt: timestamp("sent_at", { withTimezone: true }),
    sentBy: text("sent_by", { enum: ["human", "auto"] }),
    finalSubject: text("final_subject"),
    finalBody: text("final_body"),
    // Unchanged = equal after ignoring whitespace and the signature; any other edit counts
    edited: boolean("edited"),
    // Share of characters changed, 0 to 1: the learning metric
    editRatio: real("edit_ratio"),
    sampledForReview: boolean("sampled_for_review").notNull().default(false),
    reviewResult: text("review_result", { enum: ["good", "bad"] }),
    reviewedAt: timestamp("reviewed_at", { withTimezone: true }),
    // Set once the worker has turned this draft's edit into rules
    learnedAt: timestamp("learned_at", { withTimezone: true }),
    learnError: text("learn_error"),
    sendError: text("send_error"),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (table) => [
    index("drafts_email_idx").on(table.emailId),
    index("drafts_status_idx").on(table.status),
  ],
);

/** Learned rules per category, one row per version. Rollback = point the category at an
 *  older version; nothing is ever overwritten. */
export const playbooks = inbox.table(
  "playbooks",
  {
    id: serial("id").primaryKey(),
    categoryKey: text("category_key").notNull(),
    version: integer("version").notNull(),
    rules: jsonb("rules").$type<PlaybookRule[]>().notNull(),
    note: text("note").notNull(),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (table) => [unique("playbooks_category_version_key").on(table.categoryKey, table.version)],
);

/** Each category's way toward auto-send. */
export const categoryState = inbox.table("category_state", {
  categoryKey: text("category_key").primaryKey(),
  uneditedStreak: integer("unedited_streak").notNull().default(0),
  state: text("state", { enum: CATEGORY_STATES }).notNull().default("manual"),
  // 0 = no rules yet
  activePlaybookVersion: integer("active_playbook_version").notNull().default(0),
  lastReason: text("last_reason"),
  updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
});

/** Why autonomy changed, for the Learning screen and DESIGN.md. */
export const autonomyEvents = inbox.table("autonomy_events", {
  id: serial("id").primaryKey(),
  categoryKey: text("category_key").notNull(),
  kind: text("kind", { enum: AUTONOMY_EVENTS }).notNull(),
  detail: text("detail").notNull(),
  draftId: integer("draft_id"),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
});
