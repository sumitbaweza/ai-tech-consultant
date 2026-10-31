// The inbox's vocabulary: labels, states and shapes shared by the schema, the pipeline and the
// screens. Plain data and types only, so the Drizzle schema can import it too.

/** What the email wants. Test 1 groups emails by intent. */
export const INTENTS = [
  "how_to",
  "bug",
  "new_project",
  "quote",
  "refund",
  "complaint",
  "follow_up",
  "billing",
  "spam",
  "other",
] as const;
export type Intent = (typeof INTENTS)[number];

export const INTENT_LABELS: Record<Intent, string> = {
  how_to: "How-to",
  bug: "Bug",
  new_project: "New project",
  quote: "Quote",
  refund: "Refund",
  complaint: "Complaint",
  follow_up: "Follow-up",
  billing: "Billing",
  spam: "Spam",
  other: "Other",
};

/** Test 2's product areas, the default list. Settings can add, rename or switch them off. */
export const DEFAULT_PRODUCT_AREAS = [
  "Filters",
  "Home Page",
  "Domain Management",
  "Header",
  "Main Menu",
  "Footer",
  "General Settings",
  "Design Settings",
  "Managers",
] as const;
/** For an email that fits none of the configured areas. */
export const OTHER_AREA = "Other";

export const SENTIMENTS = ["positive", "neutral", "negative", "angry"] as const;
export type Sentiment = (typeof SENTIMENTS)[number];

export const URGENCIES = ["low", "normal", "high"] as const;
export type Urgency = (typeof URGENCIES)[number];

/** Risks Claude reads in the email. Each one blocks auto-send. */
export const RISK_FLAGS = [
  "refund_request",
  "legal_threat",
  "angry_customer",
  "personal_data",
  "prompt_injection",
  "spam",
  "sales_pitch",
] as const;
export type RiskFlag = (typeof RISK_FLAGS)[number];

/** Risks the code finds without a model (src/lib/pipeline/guard.ts): a second, cheaper net. */
export const GUARD_FLAGS = [
  "empty",
  "huge",
  "injection_pattern",
  "personal_data_pattern",
  "legal_pattern",
] as const;
export type GuardFlag = (typeof GUARD_FLAGS)[number];

/** A draft's life: ready for a human, scheduled to auto-send (undo window), sent by a human,
 *  sent by the system, discarded, or failed to send. */
export const DRAFT_STATUSES = ["ready", "scheduled", "sent", "auto_sent", "discarded", "failed"] as const;
export type DraftStatus = (typeof DRAFT_STATUSES)[number];

/** A category's autonomy: every draft waits for a human (manual), unedited sends are being
 *  counted toward X (earning), or drafts send themselves after the undo window (auto). */
export const CATEGORY_STATES = ["manual", "earning", "auto"] as const;
export type CategoryStateName = (typeof CATEGORY_STATES)[number];

export const AUTONOMY_EVENTS = ["counted", "reset", "earned", "revoked", "rollback", "undo"] as const;
export type AutonomyEvent = (typeof AUTONOMY_EVENTS)[number];

/** What the Brain said about one question from the email. */
export type FactCheck = {
  question: string;
  // answered: facts with citations; dont_know: the Brain's honest "I don't know";
  // unavailable: the Brain was down or not configured
  status: "answered" | "dont_know" | "unavailable";
  answer: string | null;
  confidence: number | null;
  citations: { sourceId: string; title: string | null; url: string; origin: "article" | "expert" }[];
};

/** One learned rule, with the draft whose edit taught it. */
export type PlaybookRule = { text: string; why: string; sourceDraftId: number | null };

/** "Same kind" = the same intent and product area, e.g. "how_to:Header". */
export function categoryKey(intent: string, productArea: string): string {
  return `${intent}:${productArea}`;
}

export function categoryLabel(key: string): string {
  const [intent = "", area = ""] = key.split(":");
  const intentLabel = (INTENT_LABELS as Record<string, string>)[intent] ?? intent;
  return `${intentLabel} × ${area}`;
}
