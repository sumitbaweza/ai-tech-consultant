// When a category may send on its own. Pure functions: the rules are in one place, and every
// one of them has a test. The database side lives in src/lib/review.ts.
import type { AutonomyEvent, CategoryStateName, FactCheck } from "@/lib/domain";

// Intents where a human decides, because money, prices or a new project are at stake
const HUMAN_ONLY_INTENTS = new Set(["refund", "billing", "quote", "new_project", "complaint", "spam"]);
// A draft the drafter itself doubts never sends alone
export const MIN_AUTO_CONFIDENCE = 0.7;

export type BlockerInput = {
  intent: string | null;
  sentiment: string | null;
  riskFlags: string[];
  guardFlags: string[];
  brainStatus: "ok" | "partial" | "dont_know" | "unavailable" | "skipped";
  openQuestions: string[];
  confidence: number | null;
};

/** Every reason this draft must wait for a human, whatever its category earned. Empty means
 *  it may auto-send if its category is on auto-send. */
export function autoSendBlockers(input: BlockerInput): string[] {
  const reasons: string[] = [];
  for (const flag of input.riskFlags) reasons.push(`Risk: ${flag.replaceAll("_", " ")}`);
  for (const flag of input.guardFlags) reasons.push(`Guard: ${flag.replaceAll("_", " ")}`);
  if (input.sentiment === "angry" && !input.riskFlags.includes("angry_customer")) {
    reasons.push("Risk: angry customer");
  }
  if (input.intent !== null && HUMAN_ONLY_INTENTS.has(input.intent)) {
    reasons.push(`Intent "${input.intent.replaceAll("_", " ")}" needs a human`);
  }
  if (input.brainStatus === "unavailable") reasons.push("The knowledge base was unavailable");
  if (input.brainStatus === "dont_know" || input.brainStatus === "partial") {
    reasons.push("The knowledge base didn't know every answer");
  }
  if (input.brainStatus === "skipped") reasons.push("No knowledge-base facts were looked up");
  if (input.openQuestions.length > 0) reasons.push("The draft leaves questions open");
  if (input.confidence === null || input.confidence < MIN_AUTO_CONFIDENCE) {
    reasons.push(`Draft confidence below ${String(MIN_AUTO_CONFIDENCE)}`);
  }
  return reasons;
}

/** ok: every question answered; dont_know: none answered; partial: some; unavailable: the
 *  Brain couldn't be reached for at least one; skipped: there was nothing to ask. */
export function brainStatusOf(facts: FactCheck[]): BlockerInput["brainStatus"] {
  if (facts.length === 0) return "skipped";
  if (facts.some((fact) => fact.status === "unavailable")) return "unavailable";
  const answered = facts.filter((fact) => fact.status === "answered").length;
  if (answered === facts.length) return "ok";
  return answered === 0 ? "dont_know" : "partial";
}

export type CategoryProgress = { streak: number; state: CategoryStateName };
export type ProgressChange = CategoryProgress & { event: AutonomyEvent; detail: string };

/** A human sent a draft. Unchanged counts toward X; any edit resets to manual review, as
 *  test 2 requires. */
export function afterHumanSend(current: CategoryProgress, unchanged: boolean, threshold: number): ProgressChange {
  if (!unchanged) {
    return { streak: 0, state: "manual", event: "reset", detail: "A human edited the draft before sending" };
  }
  const streak = current.streak + 1;
  if (streak >= threshold) {
    const earned = current.state !== "auto";
    return {
      streak,
      state: "auto",
      event: earned ? "earned" : "counted",
      detail: earned
        ? `${String(streak)} unedited sends in a row: auto-send is on`
        : `Unedited send ${String(streak)}`,
    };
  }
  return { streak, state: "earning", event: "counted", detail: `Unedited send ${String(streak)} of ${String(threshold)}` };
}

/** Throwing a draft away is the strongest edit there is. */
export function afterDiscard(): ProgressChange {
  return { streak: 0, state: "manual", event: "reset", detail: "A human discarded the draft" };
}

/** A sampled auto-send judged bad, or an unhappy reply to one: back to manual review. */
export function afterRevoke(reason: string): ProgressChange {
  return { streak: 0, state: "manual", event: "revoked", detail: reason };
}

/** 1 in N auto-sends goes to a human: the Nth, 2Nth, ... of the category. Counting instead
 *  of random keeps it predictable and testable. */
export function isSampled(autoSendNumber: number, sampleEvery: number): boolean {
  return sampleEvery > 0 && autoSendNumber % sampleEvery === 0;
}
