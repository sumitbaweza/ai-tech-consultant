// One email through the whole pipeline:
//   guard (code) -> classify (Haiku) -> facts (the Brain) -> draft (Sonnet) -> decide.
// "Decide" is where autonomy lives: the draft waits for a human, unless its category earned
// auto-send and nothing in this email blocks it; then it's scheduled behind the undo window.
import { and, eq, gte, isNull, desc, sql } from "drizzle-orm";
import type { Db } from "@/db/client";
import { analyses, drafts, emails, mailboxes } from "@/db/schema";
import { autoSendBlockers, brainStatusOf } from "@/lib/autonomy";
import type { BrainClient } from "@/lib/brain";
import { categoryKey, categoryLabel, OTHER_AREA, type FactCheck } from "@/lib/domain";
import { activeRules } from "@/lib/learn";
import { type Claude, LLMError } from "@/lib/llm/claude";
import { CLASSIFY_PROMPT, type Classification, classifyEmail } from "@/lib/pipeline/classify";
import { DRAFT_PROMPT, draftReply } from "@/lib/pipeline/draft";
import { lookUpFacts } from "@/lib/pipeline/facts";
import { guardEmail } from "@/lib/pipeline/guard";
import { approvedExamples, categoryProgress, clearUnsentDrafts, revokeAfterUnhappyReply } from "@/lib/review";
import { getSettings, listProductAreas } from "@/lib/settings";

export type PipelineDeps = {
  claude: Pick<Claude, "call">;
  brain: BrainClient;
  now?: () => Date;
  runTag?: string;
};

export type ProcessResult =
  | { status: "drafted"; draftId: number; scheduled: boolean; blockers: string[] }
  | { status: "no_draft"; reason: string }
  | { status: "failed"; error: string };

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** Run one email through the pipeline. Safe to run again: it replaces the analysis and any
 *  unsent draft. A failure is saved on the analysis row and shown on screen. */
export async function processEmail(db: Db, emailId: number, deps: PipelineDeps): Promise<ProcessResult> {
  const [email] = await db.select().from(emails).where(eq(emails.id, emailId));
  if (email === undefined) throw new Error(`No email ${String(emailId)}`);
  const now = deps.now?.() ?? new Date();

  const guard = guardEmail(email.subject, email.bodyText);
  const forModel = { fromName: email.fromName, fromAddress: email.fromAddress, subject: email.subject, text: guard.text };

  // 1. Classify. An empty email has nothing to classify: it gets a neutral label and a short
  //    draft asking what the sender needs.
  let classification: Classification;
  if (guard.flags.includes("empty") && email.subject.trim() === "") {
    classification = {
      intent: "other",
      product_area: OTHER_AREA,
      sentiment: "neutral",
      urgency: "low",
      language: "en",
      risk_flags: [],
      questions: [],
      summary: "Empty email: no subject and no text.",
    };
  } else {
    const areas = (await listProductAreas(db, true)).map((area) => area.name);
    try {
      classification = await classifyEmail(deps.claude, forModel, areas, deps.runTag);
    } catch (error) {
      if (!(error instanceof LLMError)) throw error;
      await saveAnalysis(db, emailId, { status: "failed", guardFlags: guard.flags, error: errorText(error) });
      return { status: "failed", error: errorText(error) };
    }
  }
  await saveAnalysis(db, emailId, {
    status: "done",
    intent: classification.intent,
    productArea: classification.product_area,
    sentiment: classification.sentiment,
    urgency: classification.urgency,
    language: classification.language,
    riskFlags: classification.risk_flags,
    guardFlags: guard.flags,
    questions: classification.questions,
    summary: classification.summary,
    promptVersion: CLASSIFY_PROMPT.version,
  });

  // An unhappy reply to one of our auto-sends costs its category the right to auto-send
  await revokeAfterUnhappyReply(db, email.threadId, email.mailboxId, classification.sentiment);

  // Spam gets a label, not a reply
  if (classification.intent === "spam") {
    await clearUnsentDrafts(db, emailId);
    return { status: "no_draft", reason: "Classified as spam: no reply drafted" };
  }

  // 2. Facts from the Brain, one answer per question
  const facts: FactCheck[] = await lookUpFacts(deps.brain, classification.questions);

  // 3. Draft, with this category's playbook and approved replies
  const key = categoryKey(classification.intent, classification.product_area);
  const [settings, playbook, examples, progress] = await Promise.all([
    getSettings(db),
    activeRules(db, key),
    approvedExamples(db, key),
    categoryProgress(db, key),
  ]);
  let output;
  try {
    output = await draftReply(
      deps.claude,
      { voiceGuide: settings.voiceGuide, categoryLabel: categoryLabel(key), rules: playbook.rules, examples, facts, email: forModel },
      deps.runTag,
    );
  } catch (error) {
    if (!(error instanceof LLMError)) throw error;
    await db.update(analyses).set({ error: `Draft failed: ${errorText(error)}` }).where(eq(analyses.emailId, emailId));
    return { status: "failed", error: errorText(error) };
  }

  // 4. Decide: a human reviews it, or it auto-sends after the undo window
  const brainStatus = brainStatusOf(facts);
  const blockers = autoSendBlockers({
    intent: classification.intent,
    sentiment: classification.sentiment,
    riskFlags: classification.risk_flags,
    guardFlags: guard.flags,
    brainStatus,
    openQuestions: output.open_questions,
    confidence: output.confidence,
  });
  const scheduled = progress.state === "auto" && blockers.length === 0;

  await clearUnsentDrafts(db, emailId);
  const [draft] = await db
    .insert(drafts)
    .values({
      emailId,
      categoryKey: key,
      subject: output.subject,
      body: output.body,
      facts,
      brainStatus,
      confidence: output.confidence,
      openQuestions: output.open_questions,
      citedSourceIds: output.facts_used,
      promptVersion: DRAFT_PROMPT.version,
      playbookVersion: playbook.version,
      exampleDraftIds: examples.map((example) => example.id),
      blockers,
      status: scheduled ? "scheduled" : "ready",
      scheduledFor: scheduled ? new Date(now.getTime() + settings.undoMinutes * 60_000) : null,
    })
    .returning({ id: drafts.id });
  if (draft === undefined) throw new Error("The draft was not saved");
  await db.update(analyses).set({ error: null }).where(eq(analyses.emailId, emailId));
  return { status: "drafted", draftId: draft.id, scheduled, blockers };
}

type AnalysisValues = Omit<typeof analyses.$inferInsert, "emailId">;

async function saveAnalysis(db: Db, emailId: number, values: AnalysisValues): Promise<void> {
  const row = {
    intent: null,
    productArea: null,
    sentiment: null,
    urgency: null,
    language: null,
    riskFlags: [],
    questions: [],
    summary: null,
    error: null,
    promptVersion: null,
    ...values,
  };
  await db
    .insert(analyses)
    .values({ emailId, ...row })
    .onConflictDoUpdate({ target: analyses.emailId, set: { ...row, createdAt: sql`now()` } });
}

/** Inbox emails in a mailbox that the pipeline hasn't seen, newest first. */
export async function unprocessedEmails(db: Db, mailboxId: number, options: { since?: Date; limit: number }) {
  const conditions = [eq(emails.mailboxId, mailboxId), eq(emails.folder, "inbox"), isNull(analyses.emailId)];
  if (options.since) conditions.push(gte(emails.receivedAt, options.since));
  return db
    .select({ id: emails.id })
    .from(emails)
    .leftJoin(analyses, eq(analyses.emailId, emails.id))
    .where(and(...conditions))
    .orderBy(desc(emails.receivedAt))
    .limit(options.limit);
}

/** The worker's job for Gmail: new mail is processed as it arrives. Only the last 3 days, and
 *  5 a minute, so connecting an old inbox doesn't run up a bill on its history. Fixture mail
 *  is processed on demand from the screen instead (the Draft buttons). */
export async function processNewGmail(db: Db, deps: PipelineDeps, perRound = 5): Promise<number> {
  const gmailBoxes = await db.select({ id: mailboxes.id }).from(mailboxes).where(eq(mailboxes.provider, "google"));
  const since = new Date(Date.now() - 3 * 24 * 60 * 60 * 1000);
  let processed = 0;
  for (const mailbox of gmailBoxes) {
    for (const { id } of await unprocessedEmails(db, mailbox.id, { since, limit: perRound })) {
      const result = await processEmail(db, id, deps);
      console.log(`Email ${String(id)}: ${result.status}`);
      processed += 1;
    }
  }
  return processed;
}
