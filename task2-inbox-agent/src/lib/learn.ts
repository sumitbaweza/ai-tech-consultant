// Learning from edits: each edited send can add 0 to 3 rules to its category's playbook.
// Playbooks are versioned (v1, v2, ...); removing a rule or rolling back makes or picks a
// version, never edits one, so any change can be undone and every draft records the version
// it used.
import { and, asc, desc, eq, isNull, max, sql } from "drizzle-orm";
import { z } from "zod";
import type { Db } from "@/db/client";
import { analyses, autonomyEvents, categoryState, drafts, emails, playbooks } from "@/db/schema";
import { categoryLabel, type PlaybookRule } from "@/lib/domain";
import type { Claude } from "@/lib/llm/claude";
import { asData } from "@/lib/pipeline/classify";

export const LEARN_PROMPT = { name: "learn_rules", version: 1 } as const;
// A playbook stays short enough to read in one glance and to follow in every draft
export const MAX_RULES = 15;

export const LearnedRulesSchema = z.object({
  rules: z.array(z.object({ text: z.string(), why: z.string() })),
});

/** The category's row, created on first use. */
export async function ensureCategory(db: Db, key: string) {
  await db.insert(categoryState).values({ categoryKey: key }).onConflictDoNothing();
  const [row] = await db.select().from(categoryState).where(eq(categoryState.categoryKey, key));
  if (row === undefined) throw new Error(`Category ${key} was not saved`);
  return row;
}

export async function activeRules(db: Db, key: string): Promise<{ version: number; rules: PlaybookRule[] }> {
  const category = await ensureCategory(db, key);
  if (category.activePlaybookVersion === 0) return { version: 0, rules: [] };
  const [playbook] = await db
    .select({ rules: playbooks.rules })
    .from(playbooks)
    .where(and(eq(playbooks.categoryKey, key), eq(playbooks.version, category.activePlaybookVersion)));
  return { version: category.activePlaybookVersion, rules: playbook?.rules ?? [] };
}

/** Save the rules as the category's next version and make it the active one. */
export async function createPlaybookVersion(db: Db, key: string, rules: PlaybookRule[], note: string): Promise<number> {
  await ensureCategory(db, key);
  return db.transaction(async (tx) => {
    // Lock the category's row so two saves can't both take the same version number
    await tx.execute(sql`select 1 from ${categoryState} where ${categoryState.categoryKey} = ${key} for update`);
    const [latest] = await tx
      .select({ version: max(playbooks.version) })
      .from(playbooks)
      .where(eq(playbooks.categoryKey, key));
    const version = (latest?.version ?? 0) + 1;
    await tx.insert(playbooks).values({ categoryKey: key, version, rules: rules.slice(-MAX_RULES), note });
    await tx
      .update(categoryState)
      .set({ activePlaybookVersion: version, updatedAt: sql`now()` })
      .where(eq(categoryState.categoryKey, key));
    return version;
  });
}

export async function removeRule(db: Db, key: string, ruleText: string): Promise<number> {
  const { rules } = await activeRules(db, key);
  const kept = rules.filter((rule) => rule.text !== ruleText);
  if (kept.length === rules.length) throw new Error("That rule is not in the active playbook");
  return createPlaybookVersion(db, key, kept, `Removed: ${ruleText}`);
}

/** Point the category at an older version (0 = no rules). Later versions stay, so this can
 *  itself be undone. */
export async function rollbackPlaybook(db: Db, key: string, version: number): Promise<void> {
  if (version !== 0) {
    const [exists] = await db
      .select({ id: playbooks.id })
      .from(playbooks)
      .where(and(eq(playbooks.categoryKey, key), eq(playbooks.version, version)));
    if (exists === undefined) throw new Error(`No playbook v${String(version)} for ${key}`);
  }
  await ensureCategory(db, key);
  await db
    .update(categoryState)
    .set({ activePlaybookVersion: version, updatedAt: sql`now()` })
    .where(eq(categoryState.categoryKey, key));
  await db.insert(autonomyEvents).values({
    categoryKey: key,
    kind: "rollback",
    detail: `Playbook switched to v${String(version)}`,
  });
}

export async function playbookHistory(db: Db, key: string) {
  return db.select().from(playbooks).where(eq(playbooks.categoryKey, key)).orderBy(desc(playbooks.version));
}

/** Turn one edited send into new rules. Returns how many rules were added. */
export async function learnFromDraft(db: Db, draftId: number, claude: Pick<Claude, "call">, runTag?: string) {
  const [row] = await db
    .select({ draft: drafts, email: emails, analysis: analyses })
    .from(drafts)
    .innerJoin(emails, eq(emails.id, drafts.emailId))
    .leftJoin(analyses, eq(analyses.emailId, emails.id))
    .where(eq(drafts.id, draftId));
  if (row?.draft.finalBody == null) throw new Error(`Draft ${String(draftId)} has no sent text to learn from`);

  const key = row.draft.categoryKey;
  const { rules } = await activeRules(db, key);
  const existing = rules.length === 0 ? "None yet." : rules.map((rule, index) => `${String(index + 1)}. ${rule.text}`).join("\n");
  const content = [
    `<category>${categoryLabel(key)}</category>`,
    `<existing_rules>\n${existing}\n</existing_rules>`,
    asData("email", `Subject: ${row.email.subject}\n\n${row.email.bodyText}`),
    asData("ai_draft", `Subject: ${row.draft.subject}\n\n${row.draft.body}`),
    asData("sent_reply", `Subject: ${row.draft.finalSubject ?? row.draft.subject}\n\n${row.draft.finalBody}`),
  ].join("\n\n");

  const learned = await claude.call(LEARN_PROMPT.name, LEARN_PROMPT.version, content, LearnedRulesSchema, { runTag });
  const known = new Set(rules.map((rule) => rule.text.toLowerCase()));
  const fresh = learned.rules
    .map((rule) => ({ text: rule.text.trim(), why: rule.why.trim(), sourceDraftId: draftId }))
    .filter((rule) => rule.text !== "" && !known.has(rule.text.toLowerCase()))
    .slice(0, 3);

  if (fresh.length > 0) {
    await createPlaybookVersion(db, key, [...rules, ...fresh], `Learned ${String(fresh.length)} from draft ${String(draftId)}`);
  }
  await db.update(drafts).set({ learnedAt: sql`now()`, learnError: null }).where(eq(drafts.id, draftId));
  return fresh.length;
}

/** The worker's learning queue: edited sends not learned from yet, oldest first. */
export async function learnPending(db: Db, claude: Pick<Claude, "call">, limit = 3) {
  const pending = await db
    .select({ id: drafts.id })
    .from(drafts)
    .where(and(eq(drafts.status, "sent"), eq(drafts.edited, true), isNull(drafts.learnedAt), isNull(drafts.learnError)))
    .orderBy(asc(drafts.sentAt))
    .limit(limit);
  for (const { id } of pending) {
    try {
      const added = await learnFromDraft(db, id, claude);
      console.log(`Draft ${String(id)}: ${String(added)} new rule(s)`);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      await db.update(drafts).set({ learnError: message }).where(eq(drafts.id, id));
      console.error(`Learning from draft ${String(id)} failed`, error);
    }
  }
  return pending.length;
}
