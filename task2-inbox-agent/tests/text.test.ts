import { describe, expect, it } from "vitest";
import { editRatio, isUnchanged, levenshtein, stripSignature } from "@/lib/text";

const draft = "Hi Maria,\n\nGo to **Design Settings > Logo** and upload the new file.\n\nBest regards,\nThe Support Team";

describe("isUnchanged", () => {
  it("ignores whitespace and the signature", () => {
    expect(isUnchanged(draft, "Hi Maria,\nGo to **Design Settings > Logo**   and upload the new file.\n\nCheers,\nSam")).toBe(true);
  });

  it("counts any other edit, however small", () => {
    expect(isUnchanged(draft, draft.replace("new file", "new PNG file"))).toBe(false);
    expect(isUnchanged(draft, draft.replace("Hi Maria", "Hello Maria"))).toBe(false);
  });
});

describe("stripSignature", () => {
  it("keeps a 'Thanks!' that opens a long reply", () => {
    const text = ["Thanks!", "line 2", "line 3", "line 4", "line 5", "line 6", "line 7"].join("\n");
    expect(stripSignature(text)).toBe(text);
  });
});

describe("editRatio", () => {
  it("is 0 for an unedited send and grows with the edit", () => {
    expect(editRatio(draft, draft)).toBe(0);
    const small = editRatio(draft, draft.replace("new file", "new PNG file"));
    const large = editRatio(draft, "Hi Maria, a teammate will call you today about this.");
    expect(small).toBeGreaterThan(0);
    expect(small).toBeLessThan(0.1);
    expect(large).toBeGreaterThan(0.5);
  });

  it("uses the classic edit distance", () => {
    expect(levenshtein("kitten", "sitting")).toBe(3);
    expect(levenshtein("", "abc")).toBe(3);
  });
});
