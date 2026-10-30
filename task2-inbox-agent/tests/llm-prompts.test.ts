import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { loadPrompt, parseSettings, PromptNotFound } from "@/lib/llm/prompts";

const TEST_PROMPTS = join(process.cwd(), "tests", "prompts");

describe("parseSettings", () => {
  it("splits each line on its first colon only", () => {
    expect(parseSettings("model: claude-haiku-5-5\nchangelog: v2: shorter\n")).toEqual({
      model: "claude-haiku-5-5",
      changelog: "v2: shorter",
    });
  });

  it("rejects a line that isn't key: value", () => {
    expect(() => parseSettings("just words")).toThrow("Not a 'key: value' line");
  });
});

describe("loadPrompt", () => {
  it("reads the settings and keeps the body, '---' lines included", () => {
    const prompt = loadPrompt("draft_test", 1, TEST_PROMPTS);
    expect(prompt).toMatchObject({
      name: "draft_test",
      version: 1,
      model: "claude-sonnet-5-5",
      max_tokens: 2000,
    });
    expect(prompt.system).toContain("Draft a reply.\n---\nA line of three dashes");
  });

  it("names the versions that exist when one is missing", () => {
    expect(() => loadPrompt("classify_test", 9, TEST_PROMPTS)).toThrow(PromptNotFound);
    expect(() => loadPrompt("classify_test", 9, TEST_PROMPTS)).toThrow("Available: v1.md");
  });

  it("refuses a misspelt setting, and reads files with Windows line endings", () => {
    const dir = mkdtempSync(join(tmpdir(), "prompts-"));
    const settings = "model: m\r\neffort: low\r\nthinking: adaptive\r\nmax_tokens: 10\r\nchangelog: x\r\n";

    mkdirSync(join(dir, "typo"));
    writeFileSync(join(dir, "typo", "v1.md"), `---\r\n${settings}temprature: 0\r\n---\r\nHi\r\n`);
    expect(() => loadPrompt("typo", 1, dir)).toThrow();

    mkdirSync(join(dir, "crlf"));
    writeFileSync(join(dir, "crlf", "v1.md"), `---\r\n${settings}---\r\nHi\r\n`);
    expect(loadPrompt("crlf", 1, dir).system).toBe("Hi");
  });
});
