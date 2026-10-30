import { describe, expect, it } from "vitest";
import { formatListDate, initials } from "@/lib/format";

describe("formatListDate", () => {
  const now = new Date("2026-10-30T15:00:00Z");

  it("shows the time for today, the day this year, and the full date before that", () => {
    expect(formatListDate(new Date("2026-10-30T08:12:00Z"), now)).toBe("8:12 AM");
    expect(formatListDate(new Date("2026-10-29T16:20:00Z"), now)).toBe("Oct 29");
    expect(formatListDate(new Date("2025-12-31T10:00:00Z"), now)).toBe("12/31/2025");
  });
});

describe("initials", () => {
  it("takes two initials from the name, or falls back to the email", () => {
    expect(initials("Maria Lopez", "maria@example.com")).toBe("ML");
    expect(initials("Cher", null)).toBe("C");
    expect(initials("  ", "zed@example.com")).toBe("Z");
    expect(initials(null, null)).toBe("?");
  });
});
