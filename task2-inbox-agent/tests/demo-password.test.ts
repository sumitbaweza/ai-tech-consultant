import { describe, expect, it } from "vitest";
import { checkDemoPassword } from "@/lib/demo-password";

describe("checkDemoPassword", () => {
  it("accepts only the exact password, and nothing when none is set", () => {
    expect(checkDemoPassword("inbox-demo", "inbox-demo")).toBe(true);
    expect(checkDemoPassword("inbox-dem", "inbox-demo")).toBe(false);
    expect(checkDemoPassword("", "inbox-demo")).toBe(false);
    expect(checkDemoPassword("anything", undefined)).toBe(false);
  });
});
