// The Knowledge Brain (Task 1), reached over HTTP only, never by importing its code. The
// schemas below follow the Brain's own /openapi.json; Zod checks every reply, so a change on
// the Brain's side fails loudly here instead of leaking half-read data into a draft.
import { z } from "zod";
import { getEnv } from "@/env";

/** The Brain is down, slow, overloaded or not configured. Callers show "facts unavailable"
 *  and never auto-send; it is not a bug in the inbox. */
export class BrainUnavailable extends Error {}

/** The Brain answered, but not with what this client expects: a 4xx or an unknown shape.
 *  That is a bug on one side or the other, so it stays loud. */
export class BrainRequestError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
  }
}

// Exported for callers that keep a conversation (an email thread) to send as history
export const ChatTurnSchema = z.object({
  role: z.enum(["user", "assistant"]),
  content: z.string(),
});
export type ChatTurn = z.infer<typeof ChatTurnSchema>;

const CitationSchema = z.object({
  source_id: z.string(),
  // "expert" is an answer a human expert gave, which flowed back into the Brain
  origin: z.enum(["article", "expert"]),
  chunk_id: z.number().int(),
  page_id: z.number().int(),
  title: z.string().nullable(),
  url: z.string(),
  image_url: z.string().nullable(),
  snippet: z.string(),
});
export type Citation = z.infer<typeof CitationSchema>;

const SourceSchema = z.object({
  origin: z.enum(["article", "expert"]),
  chunk_id: z.number().int(),
  page_id: z.number().int(),
  title: z.string().nullable(),
  url: z.string(),
  similarity: z.number().nullable(),
  score: z.number(),
});

const AskResultSchema = z.object({
  question: z.string(),
  standalone_question: z.string(),
  // false is the Brain's honest "I don't know": an email built on it never auto-sends
  answered: z.boolean(),
  answer: z.string(),
  citations: z.array(CitationSchema),
  confidence: z.number(),
  outcome: z.enum(["answered", "low_retrieval", "not_in_sources", "no_valid_citation", "model_failed"]),
  sources: z.array(SourceSchema),
  // Set when the question went to the expert queue
  expert_task_id: z.number().int().nullable().optional(),
});
export type AskResult = z.infer<typeof AskResultSchema>;

const SearchResponseSchema = z.object({
  query: z.string(),
  mode: z.enum(["hybrid", "full_text"]),
  hits: z.array(
    z.object({
      chunk_id: z.number().int(),
      page_id: z.number().int(),
      page_kind: z.enum(["article", "listing", "expert"]),
      kind: z.enum(["text", "image"]),
      title: z.string().nullable(),
      url: z.string(),
      image_url: z.string().nullable(),
      content: z.string(),
      score: z.number(),
      similarity: z.number().nullable(),
    }),
  ),
});
export type SearchResponse = z.infer<typeof SearchResponseSchema>;

const ExpertTaskSchema = z.object({
  id: z.number().int(),
  question: z.string(),
  status: z.enum(["open", "answered", "dismissed"]),
  asked_count: z.number().int(),
});
export type ExpertTask = z.infer<typeof ExpertTaskSchema>;

const ArticleSchema = z.object({
  id: z.number().int(),
  url: z.string(),
  kind: z.enum(["article", "listing", "expert"]),
  status: z.string(),
  title: z.string().nullable(),
  category: z.string().nullable(),
  folder: z.string().nullable(),
  content_md: z.string().nullable(),
  fetched_at: z.string().nullable(),
  images: z.array(
    z.object({
      position: z.number().int(),
      url: z.string(),
      alt: z.string().nullable(),
      description: z.string().nullable(),
    }),
  ),
});
export type Article = z.infer<typeof ArticleSchema>;

export type BrainClient = {
  /** An answer with citations, or answered: false when the help center doesn't cover it. */
  ask(question: string, history?: ChatTurn[]): Promise<AskResult>;
  /** Ranked help-center passages. No LLM, so fast and free. */
  search(query: string, limit?: number): Promise<SearchResponse>;
  /** Tell the Brain an answer was wrong: it opens an expert task, and the expert's answer
   *  flows back into the knowledge base. */
  flagWrongAnswer(question: string, answer: string, history?: ChatTurn[]): Promise<ExpertTask>;
  /** One article in full, or undefined if the Brain has no such id. */
  getArticle(id: number): Promise<Article | undefined>;
};

export function createBrainClient(options: {
  baseUrl: string;
  apiKey: string | undefined;
  fetch?: typeof fetch;
  // An answer runs a Claude call on the Brain's side, so allow it time
  timeoutMs?: number;
}): BrainClient {
  const send = options.fetch ?? fetch;
  const baseUrl = options.baseUrl.replace(/\/+$/, "");
  const timeoutMs = options.timeoutMs ?? 60_000;

  async function request<T>(method: "GET" | "POST", path: string, schema: z.ZodType<T>, body?: unknown) {
    if (options.apiKey === undefined) throw new BrainUnavailable("BRAIN_API_KEY is not set in .env");
    let response: Response;
    try {
      response = await send(`${baseUrl}${path}`, {
        method,
        headers: {
          "X-API-Key": options.apiKey,
          ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(timeoutMs),
      });
    } catch (error) {
      // Refused connection, DNS failure or our timeout: the Brain is unreachable
      throw new BrainUnavailable(`The Brain did not answer ${path}`, { cause: error });
    }
    // Overloaded or broken on its side: worth retrying later, not a bug here
    if (response.status === 429 || response.status >= 500) {
      throw new BrainUnavailable(`The Brain answered ${path} with HTTP ${String(response.status)}`);
    }
    if (!response.ok) {
      const detail = await response.text();
      throw new BrainRequestError(`${path}: HTTP ${String(response.status)} ${detail}`, response.status);
    }
    const parsed = schema.safeParse(await response.json());
    if (!parsed.success) {
      throw new BrainRequestError(`${path}: unexpected reply: ${z.prettifyError(parsed.error)}`);
    }
    return parsed.data;
  }

  return {
    ask: (question, history = []) => request("POST", "/v1/ask", AskResultSchema, { question, history }),
    search: (query, limit = 8) => request("POST", "/v1/search", SearchResponseSchema, { query, limit }),
    flagWrongAnswer: (question, answer, history = []) =>
      request("POST", "/v1/expert-tasks", ExpertTaskSchema, { question, answer, history }),
    async getArticle(id) {
      try {
        return await request("GET", `/v1/articles/${String(id)}`, ArticleSchema);
      } catch (error) {
        if (error instanceof BrainRequestError && error.status === 404) return undefined;
        throw error;
      }
    },
  };
}

let shared: BrainClient | undefined;

export function getBrain(): BrainClient {
  if (shared === undefined) {
    const env = getEnv();
    shared = createBrainClient({ baseUrl: env.BRAIN_API_URL, apiKey: env.BRAIN_API_KEY });
  }
  return shared;
}
