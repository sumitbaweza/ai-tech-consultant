"use server";

// Every button on the screens calls one of these. Each checks the session (getViewer) and that
// the email or draft belongs to the signed-in mailbox: server actions are public endpoints.
import { and, eq } from "drizzle-orm";
import { revalidatePath } from "next/cache";
import { z } from "zod";
import { signOut } from "@/auth";
import { getDb } from "@/db/client";
import { emails, productAreas } from "@/db/schema";
import { getBrain } from "@/lib/brain";
import { runMailboxSync } from "@/lib/gmail/sync";
import { removeRule, rollbackPlaybook } from "@/lib/learn";
import { getClaude, LLMError } from "@/lib/llm/claude";
import { draftInMailbox } from "@/lib/mailbox";
import { processEmail, unprocessedEmails } from "@/lib/pipeline/process";
import { discardDraft, reviewSample, sendDraft, undoScheduled } from "@/lib/review";
import { deliverReply } from "@/lib/send";
import { updateSettings } from "@/lib/settings";
import { getViewer } from "@/lib/viewer";

export type ActionState = { error?: string; message?: string };

function fail(error: unknown): ActionState {
  return { error: error instanceof Error ? error.message : String(error) };
}

function refresh(): void {
  revalidatePath("/", "layout");
}

async function requireMailbox() {
  const { mailbox } = await getViewer();
  if (mailbox === undefined) throw new Error("No mailbox is loaded");
  return mailbox;
}

async function requireDraft(form: FormData): Promise<number> {
  const draftId = z.coerce.number().int().positive().parse(form.get("draftId"));
  const mailbox = await requireMailbox();
  if (!(await draftInMailbox(getDb(), draftId, mailbox.id))) throw new Error("No such draft in this mailbox");
  return draftId;
}

export async function signOutAction(): Promise<void> {
  await signOut({ redirectTo: "/signin" });
}

/** The Sync now button: sync the signed-in person's Gmail at once instead of waiting for the
 *  worker's next round. */
export async function syncNowAction(): Promise<void> {
  const { mailbox } = await getViewer();
  if (mailbox?.provider !== "google") return;
  await runMailboxSync(getDb(), mailbox.id);
  refresh();
}

/** Classify and draft one email now (or again). */
export async function processEmailAction(_state: ActionState, form: FormData): Promise<ActionState> {
  try {
    const emailId = z.coerce.number().int().positive().parse(form.get("emailId"));
    const mailbox = await requireMailbox();
    const db = getDb();
    const [email] = await db
      .select({ id: emails.id })
      .from(emails)
      .where(and(eq(emails.id, emailId), eq(emails.mailboxId, mailbox.id)));
    if (email === undefined) throw new Error("No such email in this mailbox");
    const result = await processEmail(db, emailId, { claude: getClaude(), brain: getBrain() });
    refresh();
    if (result.status === "failed") return { error: result.error };
    if (result.status === "no_draft") return { message: result.reason };
    return { message: result.scheduled ? "Drafted and scheduled to auto-send" : "Drafted" };
  } catch (error) {
    return fail(error);
  }
}

// Each email costs a few Claude calls, so one click drafts a handful, not the whole inbox
const BATCH_SIZE = 5;

/** The "Draft next 5" button: the newest inbox emails the pipeline hasn't seen. */
export async function processBatchAction(): Promise<ActionState> {
  try {
    const mailbox = await requireMailbox();
    const db = getDb();
    const pending = await unprocessedEmails(db, mailbox.id, { limit: BATCH_SIZE });
    if (pending.length === 0) return { message: "Every inbox email is already classified" };
    const deps = { claude: getClaude(), brain: getBrain() };
    // Three at a time: quicker than one by one, gentle on rate limits
    const results = [];
    for (let start = 0; start < pending.length; start += 3) {
      const batch = pending.slice(start, start + 3);
      results.push(...(await Promise.all(batch.map(({ id }) => processEmail(db, id, deps)))));
    }
    refresh();
    const failed = results.filter((result) => result.status === "failed").length;
    return { message: `Processed ${String(results.length)} email(s)${failed > 0 ? `, ${String(failed)} failed` : ""}` };
  } catch (error) {
    return error instanceof LLMError ? { error: error.message } : fail(error);
  }
}

/** Send as is, or edited: the form carries the text as it is in the editor. */
export async function sendDraftAction(_state: ActionState, form: FormData): Promise<ActionState> {
  try {
    const draftId = await requireDraft(form);
    const subject = z.string().parse(form.get("subject"));
    const body = z.string().min(1, "The reply is empty").parse(form.get("body"));
    const result = await sendDraft(getDb(), draftId, { subject, body }, deliverReply);
    refresh();
    return {
      message: result.unchanged
        ? "Sent as drafted: counted toward auto-send"
        : `Sent with edits (${String(Math.round(result.editRatio * 100))}% changed): the category's count restarts`,
    };
  } catch (error) {
    return fail(error);
  }
}

export async function discardDraftAction(_state: ActionState, form: FormData): Promise<ActionState> {
  try {
    await discardDraft(getDb(), await requireDraft(form));
    refresh();
    return { message: "Draft discarded" };
  } catch (error) {
    return fail(error);
  }
}

export async function undoScheduledAction(_state: ActionState, form: FormData): Promise<ActionState> {
  try {
    await undoScheduled(getDb(), await requireDraft(form));
    refresh();
    return { message: "Auto-send stopped: the draft waits for you" };
  } catch (error) {
    return fail(error);
  }
}

export async function reviewSampleAction(_state: ActionState, form: FormData): Promise<ActionState> {
  try {
    const verdict = z.enum(["good", "bad"]).parse(form.get("verdict"));
    await reviewSample(getDb(), await requireDraft(form), verdict);
    refresh();
    return { message: verdict === "good" ? "Marked good" : "Marked bad: the category is back on manual review" };
  } catch (error) {
    return fail(error);
  }
}

// --- Settings -----------------------------------------------------------------------------

const SettingsForm = z.object({
  autoSendAfter: z.coerce.number().int().min(1).max(1000),
  sampleEvery: z.coerce.number().int().min(1).max(1000),
  undoMinutes: z.coerce.number().int().min(1).max(1440),
  voiceGuide: z.string().max(10_000),
});

export async function saveSettingsAction(_state: ActionState, form: FormData): Promise<ActionState> {
  try {
    await requireMailbox();
    await updateSettings(getDb(), SettingsForm.parse(Object.fromEntries(form)));
    refresh();
    return { message: "Settings saved" };
  } catch (error) {
    return fail(error);
  }
}

export async function addProductAreaAction(_state: ActionState, form: FormData): Promise<ActionState> {
  try {
    await requireMailbox();
    const name = z.string().trim().min(2).max(60).parse(form.get("name"));
    if (name.toLowerCase() === "other") throw new Error('"Other" is always there');
    await getDb().insert(productAreas).values({ name }).onConflictDoUpdate({ target: productAreas.name, set: { active: true } });
    refresh();
    return { message: `Added ${name}` };
  } catch (error) {
    return fail(error);
  }
}

export async function toggleProductAreaAction(form: FormData): Promise<void> {
  await requireMailbox();
  const id = z.coerce.number().int().positive().parse(form.get("id"));
  const active = form.get("active") === "true";
  await getDb().update(productAreas).set({ active }).where(eq(productAreas.id, id));
  refresh();
}

// --- Learning -----------------------------------------------------------------------------

export async function removeRuleAction(form: FormData): Promise<void> {
  await requireMailbox();
  const key = z.string().min(3).parse(form.get("categoryKey"));
  const rule = z.string().min(1).parse(form.get("rule"));
  await removeRule(getDb(), key, rule);
  refresh();
}

export async function rollbackAction(form: FormData): Promise<void> {
  await requireMailbox();
  const key = z.string().min(3).parse(form.get("categoryKey"));
  const version = z.coerce.number().int().min(0).parse(form.get("version"));
  await rollbackPlaybook(getDb(), key, version);
  refresh();
}
