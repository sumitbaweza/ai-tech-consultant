import { describe, expect, it } from "vitest";
import { afterDiscard, afterHumanSend, autoSendBlockers, brainStatusOf, isSampled } from "@/lib/autonomy";
import type { FactCheck } from "@/lib/domain";

const safe = {
  intent: "how_to",
  sentiment: "neutral",
  riskFlags: [],
  guardFlags: [],
  brainStatus: "ok" as const,
  openQuestions: [],
  confidence: 0.9,
};

describe("autoSendBlockers", () => {
  it("lets a confident how-to answer through", () => {
    expect(autoSendBlockers(safe)).toEqual([]);
  });

  it("never auto-sends the cases the brief lists", () => {
    const cases = [
      { ...safe, intent: "refund" },
      { ...safe, riskFlags: ["legal_threat"] },
      { ...safe, sentiment: "angry" },
      { ...safe, riskFlags: ["personal_data"] },
      { ...safe, guardFlags: ["injection_pattern"] },
      { ...safe, brainStatus: "dont_know" as const },
      { ...safe, brainStatus: "unavailable" as const },
      { ...safe, openQuestions: ["Which plan are they on?"] },
      { ...safe, confidence: 0.5 },
    ];
    for (const input of cases) expect(autoSendBlockers(input).length).toBeGreaterThan(0);
  });
});

describe("brainStatusOf", () => {
  const fact = (status: FactCheck["status"]): FactCheck => ({ question: "q", status, answer: null, confidence: null, citations: [] });

  it("is ok only when every question was answered", () => {
    expect(brainStatusOf([fact("answered"), fact("answered")])).toBe("ok");
    expect(brainStatusOf([fact("answered"), fact("dont_know")])).toBe("partial");
    expect(brainStatusOf([fact("dont_know")])).toBe("dont_know");
    expect(brainStatusOf([fact("answered"), fact("unavailable")])).toBe("unavailable");
    expect(brainStatusOf([])).toBe("skipped");
  });
});

describe("afterHumanSend", () => {
  it("counts unedited sends and turns on auto-send at X", () => {
    let progress = { streak: 0, state: "manual" as const } as { streak: number; state: "manual" | "earning" | "auto" };
    for (let send = 1; send < 3; send += 1) {
      const change = afterHumanSend(progress, true, 3);
      expect(change).toMatchObject({ streak: send, state: "earning", event: "counted" });
      progress = change;
    }
    expect(afterHumanSend(progress, true, 3)).toMatchObject({ streak: 3, state: "auto", event: "earned" });
  });

  it("resets to manual review on any edit, or a discard", () => {
    expect(afterHumanSend({ streak: 19, state: "earning" }, false, 20)).toMatchObject({
      streak: 0,
      state: "manual",
      event: "reset",
    });
    expect(afterDiscard()).toMatchObject({ streak: 0, state: "manual" });
  });
});

describe("isSampled", () => {
  it("picks every Nth auto-send", () => {
    const picked = Array.from({ length: 30 }, (_, index) => index + 1).filter((number) => isSampled(number, 10));
    expect(picked).toEqual([10, 20, 30]);
  });
});
