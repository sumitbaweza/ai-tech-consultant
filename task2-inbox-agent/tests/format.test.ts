import { describe, expect, it } from "vitest";
import { formatAgo, formatListDate, initials } from "@/lib/format";

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

describe("formatAgo", () => {
  const now = new Date("2026-10-31T12:00:00Z");
  const before = (ms: number) => new Date(now.getTime() - ms);

  it("rounds down to the largest whole unit", () => {
    expect(formatAgo(before(20_000), now)).toBe("just now");
    expect(formatAgo(before(5 * 60_000 + 30_000), now)).toBe("5 min ago");
    expect(formatAgo(before(3 * 3_600_000), now)).toBe("3 h ago");
    expect(formatAgo(before(30 * 3_600_000), now)).toBe("1 day ago");
    expect(formatAgo(before(72 * 3_600_000), now)).toBe("3 days ago");
  });
});
