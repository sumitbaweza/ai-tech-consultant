// Step 1: what the email wants, which product area it's about, and how risky it is (Haiku).
import { z } from "zod";
import type { Claude } from "@/lib/llm/claude";
import { INTENTS, OTHER_AREA, RISK_FLAGS, SENTIMENTS, URGENCIES } from "@/lib/domain";

export const CLASSIFY_PROMPT = { name: "classify", version: 1 } as const;

/** The product areas come from Settings, so the schema is built per call. Lengths aren't
 *  limited here: the code trims them after, rather than failing a whole email over a 4th
 *  question. */
export function classificationSchema(areas: string[]) {
  const choices = [...new Set([...areas, OTHER_AREA])] as [string, ...string[]];
  return z.object({
    intent: z.enum(INTENTS),
    product_area: z.enum(choices),
    sentiment: z.enum(SENTIMENTS),
    urgency: z.enum(URGENCIES),
    language: z.string(),
    risk_flags: z.array(z.enum(RISK_FLAGS)),
    questions: z.array(z.string()),
    summary: z.string(),
  });
}

export type Classification = z.infer<ReturnType<typeof classificationSchema>>;

/** Customer text inside a tag, with any closing tag of the same name defused, so an email
 *  can't end its own <email> block and pose as instructions after it. */
export function asData(tag: string, text: string): string {
  const defused = text.replaceAll(new RegExp(`</\\s*${tag}\\s*>`, "gi"), `</ ${tag}>`);
  return `<${tag}>\n${defused}\n</${tag}>`;
}

export type EmailForModel = { fromName: string; fromAddress: string; subject: string; text: string };

export function emailBlock(email: EmailForModel): string {
  return asData(
    "email",
    `From: ${email.fromName} <${email.fromAddress}>\nSubject: ${email.subject || "(no subject)"}\n\n${email.text || "(empty body)"}`,
  );
}

export async function classifyEmail(
  claude: Pick<Claude, "call">,
  email: EmailForModel,
  areas: string[],
  runTag?: string,
): Promise<Classification> {
  const content = `<product_areas>\n${areas.map((area) => `- ${area}`).join("\n")}\n- ${OTHER_AREA} (none of the above)\n</product_areas>\n\n${emailBlock(email)}`;
  const result = await claude.call(
    CLASSIFY_PROMPT.name,
    CLASSIFY_PROMPT.version,
    content,
    classificationSchema(areas),
    { runTag },
  );
  return {
    ...result,
    language: result.language.toLowerCase().slice(0, 8),
    risk_flags: [...new Set(result.risk_flags)],
    questions: result.questions.map((question) => question.trim()).filter((question) => question !== "").slice(0, 3),
  };
}
