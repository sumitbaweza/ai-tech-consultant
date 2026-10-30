import { describe, expect, it } from "vitest";
import { costUsd } from "@/lib/llm/cost";

const none = { inputTokens: 0, outputTokens: 0, cacheWriteTokens: 0, cacheReadTokens: 0 };

describe("costUsd", () => {
  it("prices input, output and cache tokens per million", () => {
    // 1,000 in at $0.10 and 100 out at $0.50 per million
    expect(costUsd("claude-haiku-5-5", { ...none, inputTokens: 1000, outputTokens: 100 })).toBe("0.000150");
    // 6,000 in at $2 and 400 out at $10: the blueprint's estimate for one Brain answer
    expect(costUsd("claude-sonnet-5-5", { ...none, inputTokens: 6000, outputTokens: 400 })).toBe("0.016000");
    expect(
      costUsd("claude-opus-5-5", { ...none, cacheWriteTokens: 1_000_000, cacheReadTokens: 1_000_000 }),
    ).toBe("5.200000");
  });

  it("uses Haiku's second rate card above 100K prompt tokens, and halves batches", () => {
    expect(costUsd("claude-haiku-5-5", { ...none, inputTokens: 200_000 })).toBe("0.100000");
    expect(costUsd("claude-sonnet-5-5", { ...none, inputTokens: 1_000_000 }, true)).toBe("1.000000");
  });

  it("logs an unknown model at zero, and matches dated snapshot names", () => {
    expect(costUsd("some-other-model", { ...none, inputTokens: 1000 })).toBe("0.000000");
    expect(costUsd("claude-haiku-5-5-20261001", { ...none, inputTokens: 10_000 })).toBe("0.001000");
  });
});
