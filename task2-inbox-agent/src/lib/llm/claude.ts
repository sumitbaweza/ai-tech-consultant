// The one way the inbox calls Claude. Same design as the Brain's app/llm.py:
//
// 1. Loads a versioned prompt file (prompts/<name>/v<N>.md) with the model, effort, thinking
//    mode, max_tokens and system prompt.
// 2. Asks for JSON matching a Zod schema (structured outputs) and checks the reply with the
//    same schema.
// 3. Lets the SDK retry rate limits, overloads, server errors and timeouts with exponential
//    backoff, then tries the prompt's fallback model once.
// 4. Checks stop_reason, so a refusal or a reply cut off by max_tokens never reaches the
//    caller as if it were an answer.
// 5. Writes one row to inbox.llm_calls per API call, failures included. Every cost and speed
//    number in EVALS.md comes from that table.
//
// No temperature, top_p or top_k: since API version 2026-10-21 they are an error on every model.
import Anthropic from "@anthropic-ai/sdk";
import { betaZodOutputFormat } from "@anthropic-ai/sdk/helpers/beta/zod";
import type {
  BetaContentBlockParam,
  BetaMessage,
  BetaTextBlock,
  MessageCreateParamsNonStreaming,
} from "@anthropic-ai/sdk/resources/beta/messages/messages";
import { z } from "zod";
import { type Db, getDb } from "@/db/client";
import { llmCalls } from "@/db/schema";
import { getEnv } from "@/env";
import { costUsd } from "@/lib/llm/cost";
import { loadPrompt, PROMPTS_DIR, type Prompt, promptLabel } from "@/lib/llm/prompts";

/** What a prompt runs on: plain text, or content blocks such as an image plus text. */
export type Content = string | BetaContentBlockParam[];

// --- Errors ---------------------------------------------------------------------------------

/** A call that produced no usable answer. Callers turn it into "no draft", never a guess. */
export class LLMError extends Error {}
/** Claude declined to answer (stop_reason "refusal"). */
export class LLMRefusal extends LLMError {}
/** The reply hit max_tokens even after one retry with twice the room. */
export class LLMTruncated extends LLMError {}
/** The reply wasn't JSON matching the schema. */
export class LLMBadOutput extends LLMError {}
/** No key, or the model and its fallback both kept failing after retries. */
export class LLMUnavailable extends LLMError {}

// Sonnet and Opus can re-run a refused request on a substitute model that Anthropic picks.
// It's a beta feature, so every call uses the SDK's beta namespace; Haiku has no such fallback.
const SERVER_FALLBACK_MODELS = new Set(["claude-sonnet-5-5", "claude-opus-5-5"]);
const SERVER_FALLBACK_BETA = "server-side-fallback-2026-07-01";

/** Still failing after the SDK's own retries, which a different model may avoid.
 *  InternalServerError covers every 5xx, 529 "overloaded" included; APIConnectionError
 *  covers timeouts. Check the SDK's classes, never the message text. */
export function isTransient(error: unknown): boolean {
  return (
    error instanceof Anthropic.RateLimitError ||
    error instanceof Anthropic.InternalServerError ||
    error instanceof Anthropic.APIConnectionError
  );
}

// --- Requests and replies -------------------------------------------------------------------

export function requestParams<T>(
  prompt: Prompt,
  model: string,
  content: Content,
  schema: z.ZodType<T>,
  maxTokens: number,
): MessageCreateParamsNonStreaming {
  const params: MessageCreateParamsNonStreaming = {
    model,
    max_tokens: maxTokens,
    system: prompt.system,
    // The email, transcript or question goes in as the user's content: data to work on.
    // The system prompt says so too, and never mixes customer text into its instructions.
    messages: [{ role: "user", content }],
    output_config: { effort: prompt.effort, format: betaZodOutputFormat(schema) },
    thinking: { type: prompt.thinking },
  };
  if (SERVER_FALLBACK_MODELS.has(model)) {
    params.betas = [SERVER_FALLBACK_BETA];
    params.fallbacks = "default";
  }
  return params;
}

/** Turn a reply into a checked object, or throw if it isn't a complete answer. */
export function readReply<T>(message: BetaMessage, schema: z.ZodType<T>): T {
  switch (message.stop_reason) {
    case "end_turn":
      break;
    case "refusal":
      throw new LLMRefusal(
        `Claude declined to answer (category: ${message.stop_details?.category ?? "none"})`,
      );
    case "max_tokens":
      throw new LLMTruncated(`The reply hit max_tokens after ${String(message.usage.output_tokens)} tokens`);
    default:
      throw new LLMError(`Unexpected stop_reason: ${String(message.stop_reason)}`);
  }

  // Thinking blocks come first; the JSON answer is the text block
  const text = message.content.find((block): block is BetaTextBlock => block.type === "text");
  if (text === undefined) throw new LLMBadOutput("The reply has no text block");
  let json: unknown;
  try {
    json = JSON.parse(text.text);
  } catch {
    throw new LLMBadOutput("The reply isn't valid JSON");
  }
  const parsed = schema.safeParse(json);
  if (!parsed.success) {
    throw new LLMBadOutput(`The reply doesn't match the schema: ${z.prettifyError(parsed.error)}`);
  }
  return parsed.data;
}

// --- The ledger -----------------------------------------------------------------------------

export type LlmCallRow = typeof llmCalls.$inferInsert;

export type Ledger = { record(row: LlmCallRow): Promise<void> };

/** Saves each row on its own, outside the caller's transaction, so it's kept even if the
 *  caller's work rolls back. A failed save is logged, never thrown: it must not break a draft. */
export function databaseLedger(db: Db): Ledger {
  return {
    async record(row) {
      try {
        await db.insert(llmCalls).values(row);
      } catch (error) {
        console.error(`Could not save the llm_calls row for ${row.prompt}`, error);
      }
    },
  };
}

// --- The client -----------------------------------------------------------------------------

/** One non-streaming Messages API call. The real one is the SDK; tests pass a fake. */
export type SendMessage = (params: MessageCreateParamsNonStreaming) => Promise<BetaMessage>;

export class Claude {
  constructor(
    private readonly send: SendMessage,
    private readonly ledger: Ledger,
    private readonly promptsDir = PROMPTS_DIR,
  ) {}

  /** Run one prompt and return its reply as a checked object. Throws an LLMError subclass
   *  when there is no usable answer. */
  async call<T>(
    name: string,
    version: number,
    content: Content,
    schema: z.ZodType<T>,
    options: { runTag?: string } = {},
  ): Promise<T> {
    const prompt = loadPrompt(name, version, this.promptsDir);
    const models = prompt.fallback_model ? [prompt.model, prompt.fallback_model] : [prompt.model];
    let lastError: unknown;
    for (const model of models) {
      try {
        return await this.callModel(prompt, model, content, schema, options.runTag);
      } catch (error) {
        if (!isTransient(error)) throw error;
        console.warn(`${promptLabel(prompt)} on ${model} failed after retries`, error);
        lastError = error;
      }
    }
    throw new LLMUnavailable(`${promptLabel(prompt)}: no model answered (${models.join(", ")})`, {
      cause: lastError,
    });
  }

  private async callModel<T>(
    prompt: Prompt,
    model: string,
    content: Content,
    schema: z.ZodType<T>,
    runTag: string | undefined,
  ): Promise<T> {
    let maxTokens = prompt.max_tokens;
    for (;;) {
      const started = performance.now();
      let message: BetaMessage;
      try {
        message = await this.send(requestParams(prompt, model, content, schema, maxTokens));
      } catch (error) {
        await this.record(prompt, model, started, runTag, undefined, error);
        throw error;
      }
      try {
        const result = readReply(message, schema);
        await this.record(prompt, model, started, runTag, message);
        return result;
      } catch (error) {
        await this.record(prompt, model, started, runTag, message, error);
        // A cut-off reply gets one more try with twice the room
        if (error instanceof LLMTruncated && maxTokens === prompt.max_tokens) {
          maxTokens *= 2;
          continue;
        }
        throw error;
      }
    }
  }

  /** One ledger row. The model is the one that answered, which a refusal fallback can change. */
  private async record(
    prompt: Prompt,
    requestedModel: string,
    started: number,
    runTag: string | undefined,
    message?: BetaMessage,
    error?: unknown,
  ): Promise<void> {
    const model = message?.model ?? requestedModel;
    const usage = {
      inputTokens: message?.usage.input_tokens ?? 0,
      outputTokens: message?.usage.output_tokens ?? 0,
      cacheWriteTokens: message?.usage.cache_creation_input_tokens ?? 0,
      cacheReadTokens: message?.usage.cache_read_input_tokens ?? 0,
    };
    await this.ledger.record({
      prompt: prompt.name,
      promptVersion: prompt.version,
      model,
      ...usage,
      costUsd: costUsd(model, usage),
      latencyMs: Math.round(performance.now() - started),
      success: error === undefined,
      stopReason: message?.stop_reason ?? null,
      error: error === undefined ? null : describeError(error),
      runTag: runTag ?? null,
    });
  }
}

function describeError(error: unknown): string {
  return error instanceof Error ? `${error.constructor.name}: ${error.message}` : String(error);
}

let shared: Claude | undefined;

/** The app's shared client: one connection pool per process, logging to the database. */
export function getClaude(): Claude {
  if (shared === undefined) {
    const apiKey = getEnv().ANTHROPIC_API_KEY;
    if (apiKey === undefined) throw new LLMUnavailable("ANTHROPIC_API_KEY is not set in .env");
    const client = new Anthropic({
      apiKey,
      // The SDK retries 408, 409, 429, 5xx and timeouts with exponential backoff
      maxRetries: 3,
      timeout: 90_000, // milliseconds per attempt
    });
    shared = new Claude((params) => client.beta.messages.create(params), databaseLedger(getDb()));
  }
  return shared;
}
