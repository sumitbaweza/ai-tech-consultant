// Step 3: draft the reply (Sonnet) from the facts, the voice guide, the category's learned
// rules and replies a human already approved.
import { z } from "zod";
import type { FactCheck, PlaybookRule } from "@/lib/domain";
import type { Claude } from "@/lib/llm/claude";
import { asData, type EmailForModel, emailBlock } from "@/lib/pipeline/classify";
import { factId } from "@/lib/pipeline/facts";

export const DRAFT_PROMPT = { name: "draft", version: 1 } as const;

export const DraftSchema = z.object({
  subject: z.string(),
  body: z.string(),
  facts_used: z.array(z.string()),
  open_questions: z.array(z.string()),
  confidence: z.number(),
});
export type DraftOutput = z.infer<typeof DraftSchema>;

export type DraftInput = {
  voiceGuide: string;
  categoryLabel: string;
  rules: PlaybookRule[];
  examples: { subject: string; body: string }[];
  facts: FactCheck[];
  email: EmailForModel;
};

export function factsBlock(facts: FactCheck[]): string {
  if (facts.length === 0) return "<facts>\nThe email asked nothing the help center could answer.\n</facts>";
  const items = facts.map((fact, index) => {
    const header = `<fact question="${fact.question.replaceAll('"', "'")}" status="${fact.status}">`;
    if (fact.status !== "answered") return `${header}\n(no answer)\n</fact>`;
    const sources = fact.citations
      .map((citation) => `- ${factId(index, citation.sourceId)}: ${citation.title ?? "Untitled"} (${citation.url})`)
      .join("\n");
    return `${header}\n${fact.answer ?? ""}\nSources:\n${sources}\n</fact>`;
  });
  return `<facts>\n${items.join("\n")}\n</facts>`;
}

export function buildDraftContent(input: DraftInput): string {
  const rules =
    input.rules.length === 0
      ? "No rules learned yet."
      : input.rules.map((rule, index) => `${String(index + 1)}. ${rule.text}`).join("\n");
  const examples =
    input.examples.length === 0
      ? "No approved replies yet."
      : input.examples
          .map((example) => asData("example", `Subject: ${example.subject}\n\n${example.body}`))
          .join("\n");
  return [
    `<voice_guide>\n${input.voiceGuide}\n</voice_guide>`,
    `<playbook_rules category="${input.categoryLabel}">\n${rules}\n</playbook_rules>`,
    `<approved_examples>\n${examples}\n</approved_examples>`,
    factsBlock(input.facts),
    emailBlock(input.email),
  ].join("\n\n");
}

/** The draft, with only fact ids that really exist (an invented id is dropped, never shown
 *  as a citation) and a confidence kept between 0 and 1. */
export async function draftReply(
  claude: Pick<Claude, "call">,
  input: DraftInput,
  runTag?: string,
): Promise<DraftOutput> {
  const output = await claude.call(DRAFT_PROMPT.name, DRAFT_PROMPT.version, buildDraftContent(input), DraftSchema, {
    runTag,
  });
  const known = new Set(
    input.facts.flatMap((fact, index) => fact.citations.map((citation) => factId(index, citation.sourceId))),
  );
  return {
    ...output,
    facts_used: [...new Set(output.facts_used)].filter((id) => known.has(id)),
    open_questions: output.open_questions.filter((question) => question.trim() !== ""),
    confidence: Math.min(1, Math.max(0, output.confidence)),
  };
}
