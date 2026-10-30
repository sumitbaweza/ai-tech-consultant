// Versioned prompt files: prompts/<name>/v<N>.md. Settings between two --- lines at the top,
// then the system prompt. A change means a new version, never an edit, so each logged call
// points at the exact text it used. Same format as the Brain's prompts (Task 1).
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { z } from "zod";

export const PROMPTS_DIR = join(process.cwd(), "prompts");

const SettingsSchema = z
  .object({
    model: z.string().min(1),
    fallback_model: z.string().min(1).optional(),
    effort: z.enum(["low", "medium", "high", "xhigh", "max"]),
    thinking: z.enum(["adaptive", "disabled", "between_tools"]),
    max_tokens: z.coerce.number().int().positive(),
    changelog: z.string().min(1),
  })
  // A misspelt setting is an error, not silently ignored
  .strict();

export type Prompt = z.infer<typeof SettingsSchema> & {
  name: string;
  version: number;
  system: string;
};

export class PromptNotFound extends Error {}

/** The settings block: one `key: value` per line. Values may contain colons; only the first
 *  one splits. No YAML library, because the block never needs more than this. */
export function parseSettings(block: string): Record<string, string> {
  const settings: Record<string, string> = {};
  for (const line of block.split("\n")) {
    if (line.trim() === "") continue;
    const colon = line.indexOf(":");
    if (colon <= 0) throw new Error(`Not a 'key: value' line: ${line}`);
    settings[line.slice(0, colon).trim()] = line.slice(colon + 1).trim();
  }
  return settings;
}

const cache = new Map<string, Prompt>();

export function loadPrompt(name: string, version: number, promptsDir = PROMPTS_DIR): Prompt {
  const path = join(promptsDir, name, `v${String(version)}.md`);
  const cached = cache.get(path);
  if (cached) return cached;

  if (!existsSync(path)) {
    const folder = join(promptsDir, name);
    const available = existsSync(folder) ? readdirSync(folder).filter((file) => /^v\d+\.md$/.test(file)) : [];
    throw new PromptNotFound(
      `No v${String(version)}.md for prompt '${name}'. Available: ${available.join(", ") || "none"}`,
    );
  }

  // Windows checkouts may have CRLF line endings; the format is defined with \n
  const text = readFileSync(path, "utf8").replace(/\r\n/g, "\n");
  const parts = text.split("---\n");
  if (parts.length < 3 || parts[0] !== "") {
    throw new Error(`${path}: put the settings between two '---' lines at the top`);
  }
  const settings = SettingsSchema.parse(parseSettings(parts[1] ?? ""));
  // The prompt body may itself contain '---', so rejoin everything after the settings
  const system = parts.slice(2).join("---\n").trim();

  const prompt: Prompt = { ...settings, name, version, system };
  cache.set(path, prompt);
  return prompt;
}

export function promptLabel(prompt: Pick<Prompt, "name" | "version">): string {
  return `${prompt.name}@v${String(prompt.version)}`;
}
