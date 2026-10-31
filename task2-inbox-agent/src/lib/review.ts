// What happens to a draft after it's written: a human sends it as is, edits then sends it, or
// discards it; or, in a category that earned it, it sends itself after the undo window. Each
// outcome moves the category's streak (src/lib/autonomy.ts holds the rules).
import { and, count, eq, inArray, lte, ne, sql } from "drizzle-orm";
import type { Db } from "@/db/client";
import { autonomyEvents, categoryState, drafts, emails } from "@/db/schema";
import {
  afterDiscard,
  afterHumanSend,
  afterRevoke,
  type CategoryProgress,
  isSampled,
  type ProgressChange,
} from "@/lib/autonomy";
import { ensureCategory } from "@/lib/learn";
import type { Deliver } from "@/lib/send";
import { getSettings } from "@/lib/settings";
import { editRatio, isUnchanged } from "@/lib/text";

export async function categoryProgress(db: Db, key: string): Promise<CategoryProgress> {
  const row = await ensureCategory(db, key);
  return { streak: row.uneditedStreak, state: row.state };
}

async function recordChange(db: Db, key: string, change: ProgressChange, draftId: number | null) {
  await db
    .update(categoryState)
    .set({ uneditedStreak: change.streak, state: change.state, lastReason: change.detail, updatedAt: sql`now()` })
    .where(eq(categoryState.categoryKey, key));
  await db.insert(autonomyEvents).values({ categoryKey: key, kind: change.event, detail: change.detail, draftId });
}

async function loadDraft(db: Db, draftId: number) {
  const [row] = await db
    .select({ draft: drafts, email: emails })
    .from(drafts)
    .innerJoin(emails, eq(emails.id, drafts.emailId))
    .where(eq(drafts.id, draftId));
  if (row === undefined) throw new Error(`No draft ${String(draftId)}`);
  return row;
}

/** A human sends the draft, as is or edited. The send happens first: if it fails, nothing is
 *  counted and the draft stays ready with the error shown. */
export async function sendDraft(
  db: Db,
  draftId: number,
  final: { subject: string; body: string },
  deliver: Deliver,
): Promise<{ unchanged: boolean; editRatio: number }> {
  const { draft, email } = await loadDraft(db, draftId);
  if (draft.status !== "ready" && draft.status !== "scheduled" && draft.status !== "failed") {
    throw new Error(`Draft ${String(draftId)} is already ${draft.status}`);
  }
  const subject = final.subject.trim() === "" ? draft.subject : final.subject;
  const unchanged = isUnchanged(draft.body, final.body) && subject.trim() === draft.subject.trim();
  const ratio = unchanged ? 0 : editRatio(`${draft.subject}\n${draft.body}`, `${subject}\n${final.body}`);

  try {
    await deliver(db, { draftId, original: email, subject, body: final.body });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    await db.update(drafts).set({ status: "failed", sendError: message }).where(eq(drafts.id, draftId));
    throw error;
  }

  await db
    .update(drafts)
    .set({
      status: "sent",
      sentBy: "human",
      sentAt: sql`now()`,
      scheduledFor: null,
      finalSubject: subject,
      finalBody: final.body,
      edited: !unchanged,
      editRatio: ratio,
      sendError: null,
    })
    .where(eq(drafts.id, draftId));

  const settings = await getSettings(db);
  const change = afterHumanSend(await categoryProgress(db, draft.categoryKey), unchanged, settings.autoSendAfter);
  await recordChange(db, draft.categoryKey, change, draftId);
  return { unchanged, editRatio: ratio };
}

export async function discardDraft(db: Db, draftId: number): Promise<void> {
  const { draft } = await loadDraft(db, draftId);
  if (draft.status === "sent" || draft.status === "auto_sent") throw new Error("A sent draft can't be discarded");
  await db.update(drafts).set({ status: "discarded", scheduledFor: null }).where(eq(drafts.id, draftId));
  await recordChange(db, draft.categoryKey, afterDiscard(), draftId);
}

/** Stop an auto-send inside its undo window: the draft goes back to a human. */
export async function undoScheduled(db: Db, draftId: number): Promise<void> {
  const { draft } = await loadDraft(db, draftId);
  if (draft.status !== "scheduled") throw new Error("Only a scheduled send can be undone");
  await db.update(drafts).set({ status: "ready", scheduledFor: null }).where(eq(drafts.id, draftId));
  await db.insert(autonomyEvents).values({
    categoryKey: draft.categoryKey,
    kind: "undo",
    detail: "A human stopped an auto-send in its undo window",
    draftId,
  });
}

/** A human checks a sampled auto-send. A bad one switches the category back to manual review. */
export async function reviewSample(db: Db, draftId: number, verdict: "good" | "bad"): Promise<void> {
  const { draft } = await loadDraft(db, draftId);
  if (draft.status !== "auto_sent") throw new Error("Only auto-sent drafts are reviewed");
  await db.update(drafts).set({ reviewResult: verdict, reviewedAt: sql`now()` }).where(eq(drafts.id, draftId));
  if (verdict === "bad") {
    await recordChange(db, draft.categoryKey, afterRevoke("A sampled auto-send was judged bad"), draftId);
  }
}

/** Revoke auto-send when a customer answers an auto-sent reply unhappily. */
export async function revokeAfterUnhappyReply(db: Db, threadId: string, mailboxId: number, sentiment: string) {
  if (sentiment !== "negative" && sentiment !== "angry") return;
  const autoSent = await db
    .select({ id: drafts.id, categoryKey: drafts.categoryKey })
    .from(drafts)
    .innerJoin(emails, eq(emails.id, drafts.emailId))
    .where(and(eq(emails.threadId, threadId), eq(emails.mailboxId, mailboxId), eq(drafts.status, "auto_sent")));
  for (const draft of autoSent) {
    const current = await categoryProgress(db, draft.categoryKey);
    if (current.state === "manual") continue;
    await recordChange(db, draft.categoryKey, afterRevoke("The customer replied unhappily to an auto-sent email"), draft.id);
  }
}

/** The worker's job: send the scheduled drafts whose undo window has passed. A category that
 *  lost auto-send meanwhile hands its drafts back to a human instead. */
export async function sendDueDrafts(db: Db, deliver: Deliver, now = new Date()): Promise<number> {
  const due = await db
    .select({ id: drafts.id })
    .from(drafts)
    .where(and(eq(drafts.status, "scheduled"), lte(drafts.scheduledFor, now)));
  const settings = await getSettings(db);
  let sent = 0;
  for (const { id } of due) {
    const { draft, email } = await loadDraft(db, id);
    const progress = await categoryProgress(db, draft.categoryKey);
    if (progress.state !== "auto") {
      await db.update(drafts).set({ status: "ready", scheduledFor: null }).where(eq(drafts.id, id));
      continue;
    }
    try {
      await deliver(db, { draftId: id, original: email, subject: draft.subject, body: draft.body });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      await db.update(drafts).set({ status: "failed", sendError: message }).where(eq(drafts.id, id));
      continue;
    }
    // This send's number in its category decides whether a human samples it
    const [previous] = await db
      .select({ total: count() })
      .from(drafts)
      .where(and(eq(drafts.categoryKey, draft.categoryKey), eq(drafts.status, "auto_sent")));
    const number = (previous?.total ?? 0) + 1;
    await db
      .update(drafts)
      .set({
        status: "auto_sent",
        sentBy: "auto",
        sentAt: sql`now()`,
        finalSubject: draft.subject,
        finalBody: draft.body,
        edited: false,
        editRatio: 0,
        sampledForReview: isSampled(number, settings.sampleEvery),
      })
      .where(eq(drafts.id, id));
    sent += 1;
  }
  return sent;
}

/** Drafts for this email that a new draft replaces: unsent ones only. */
export async function clearUnsentDrafts(db: Db, emailId: number): Promise<void> {
  await db
    .delete(drafts)
    .where(and(eq(drafts.emailId, emailId), inArray(drafts.status, ["ready", "failed", "scheduled"])));
}

/** Replies a human approved in this category, newest first: examples for the drafter.
 *  Auto-sends judged bad are left out. */
export async function approvedExamples(db: Db, key: string, limit = 3) {
  const rows = await db
    .select({ id: drafts.id, subject: drafts.finalSubject, body: drafts.finalBody })
    .from(drafts)
    .where(
      and(
        eq(drafts.categoryKey, key),
        inArray(drafts.status, ["sent", "auto_sent"]),
        sql`${drafts.reviewResult} is distinct from 'bad'`,
        ne(drafts.finalBody, ""),
      ),
    )
    .orderBy(sql`${drafts.sentAt} desc`)
    .limit(limit);
  return rows.flatMap((row) =>
    row.subject === null || row.body === null ? [] : [{ id: row.id, subject: row.subject, body: row.body }],
  );
}
