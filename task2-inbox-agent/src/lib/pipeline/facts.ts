// Step 2: ask the Knowledge Brain each question from the email, over its HTTP API.
import { type BrainClient, BrainRequestError, BrainUnavailable } from "@/lib/brain";
import type { FactCheck } from "@/lib/domain";

/** One Brain answer per question, asked in parallel. A Brain that is down marks the facts
 *  unavailable instead of failing the email: it still gets a draft, which never auto-sends. */
export async function lookUpFacts(brain: BrainClient, questions: string[]): Promise<FactCheck[]> {
  return Promise.all(
    questions.map(async (question): Promise<FactCheck> => {
      try {
        const result = await brain.ask(question);
        return {
          question,
          status: result.answered ? "answered" : "dont_know",
          answer: result.answered ? result.answer : null,
          confidence: result.confidence,
          citations: result.citations.map((citation) => ({
            sourceId: citation.source_id,
            title: citation.title,
            url: citation.url,
            origin: citation.origin,
          })),
        };
      } catch (error) {
        if (!(error instanceof BrainUnavailable || error instanceof BrainRequestError)) throw error;
        // A request error is a bug between the two services: log it loudly, but the email
        // still gets a draft, with the facts marked unavailable
        if (error instanceof BrainRequestError) console.error("Brain request failed", error);
        return { question, status: "unavailable", answer: null, confidence: null, citations: [] };
      }
    }),
  );
}

/** Fact ids as the drafter sees them: F1.S2 is source S2 of the answer to question 1. The
 *  Brain numbers sources per answer, so the question number keeps them unique. */
export function factId(questionIndex: number, sourceId: string): string {
  return `F${String(questionIndex + 1)}.${sourceId}`;
}
