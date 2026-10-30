// What a Claude call cost, for the llm_calls ledger. Same prices and rules as the Brain's
// wrapper (task1-knowledge-brain/app/llm.py), so the tasks' costs compare directly.

type Price = {
  // US dollars per million tokens
  input: number;
  output: number;
  cacheWrite: number; // 5-minute cache writes: 1.25 times the input price
  cacheRead: number;
};

// From Claude's pricing docs, checked Oct 30, 2026
const PRICES: Record<string, Price> = {
  "claude-haiku-5-5": { input: 0.1, output: 0.5, cacheWrite: 0.125, cacheRead: 0.01 },
  "claude-sonnet-5-5": { input: 2, output: 10, cacheWrite: 2.5, cacheRead: 0.2 },
  "claude-opus-5-5": { input: 4, output: 20, cacheWrite: 5, cacheRead: 0.2 },
};
// Haiku 5.5 switches to a second rate card when the prompt is over 100K tokens
const HAIKU_LONG_PROMPT: Price = { input: 0.5, output: 2.5, cacheWrite: 0.625, cacheRead: 0.05 };
const LONG_PROMPT_TOKENS = 100_000;

export type Usage = {
  inputTokens: number;
  outputTokens: number;
  cacheWriteTokens: number;
  cacheReadTokens: number;
};

/** Dollars as a string with 6 decimals, ready for the numeric(12, 6) column. An unknown model
 *  costs "0.000000" and logs a warning, so it stands out in the ledger. */
export function costUsd(model: string, usage: Usage, batch = false): string {
  // startsWith also matches a dated snapshot name such as claude-haiku-5-5-20261001
  let price = Object.entries(PRICES).find(([name]) => model.startsWith(name))?.[1];
  if (price === undefined) {
    console.warn(`No price for model ${model}; logging its cost as $0`);
    return (0).toFixed(6);
  }
  const promptTokens = usage.inputTokens + usage.cacheWriteTokens + usage.cacheReadTokens;
  if (model.startsWith("claude-haiku-5-5") && promptTokens > LONG_PROMPT_TOKENS) {
    price = HAIKU_LONG_PROMPT;
  }
  const total =
    (usage.inputTokens * price.input +
      usage.outputTokens * price.output +
      usage.cacheWriteTokens * price.cacheWrite +
      usage.cacheReadTokens * price.cacheRead) /
    1_000_000;
  // Message Batches cost half of every rate
  return (batch ? total / 2 : total).toFixed(6);
}
