// Classify and draft one email from the command line, the same way the screen's button does:
//   pnpm draft-email <email id>
//   docker compose exec inbox-worker pnpm draft-email <email id>
// Handy for a demo or a smoke test; it costs one Haiku call, the Brain's answers and one
// Sonnet call.
import { eq } from "drizzle-orm";
import { getDb } from "@/db/client";
import { analyses, drafts, emails } from "@/db/schema";
import { getBrain } from "@/lib/brain";
import { getClaude } from "@/lib/llm/claude";
import { processEmail } from "@/lib/pipeline/process";
import { loadRootEnv } from "@/root-env";

loadRootEnv();

const emailId = Number(process.argv[2]);
if (!Number.isSafeInteger(emailId) || emailId <= 0) {
  console.error("Usage: pnpm draft-email <email id>");
  process.exit(1);
}

// The shared pool: the Claude wrapper logs its ledger rows through it too, so closing it at
// the end lets the process exit
const db = getDb();
try {
  const [email] = await db.select().from(emails).where(eq(emails.id, emailId));
  if (email === undefined) throw new Error(`No email ${String(emailId)}`);
  console.log(`Email ${String(emailId)}: "${email.subject}" from ${email.fromAddress}`);

  const result = await processEmail(db, emailId, { claude: getClaude(), brain: getBrain(), runTag: "draft-email" });
  console.log("Result:", result);

  const [analysis] = await db.select().from(analyses).where(eq(analyses.emailId, emailId));
  console.log("Analysis:", {
    intent: analysis?.intent,
    area: analysis?.productArea,
    sentiment: analysis?.sentiment,
    language: analysis?.language,
    risks: [...(analysis?.riskFlags ?? []), ...(analysis?.guardFlags ?? [])],
    questions: analysis?.questions,
  });
  if (result.status === "drafted") {
    const [draft] = await db.select().from(drafts).where(eq(drafts.id, result.draftId));
    if (draft !== undefined) {
      for (const fact of draft.facts) {
        console.log(`Fact: ${fact.question} -> ${fact.status} (${String(fact.citations.length)} citation(s))`);
      }
      console.log(`\n--- Draft (${draft.brainStatus}, confidence ${String(draft.confidence)}) ---\n${draft.subject}\n\n${draft.body}\n`);
    }
  }
} finally {
  await db.$client.end();
}
