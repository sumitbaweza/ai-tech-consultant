// "Unchanged" and "edit size", the two numbers the learning loop runs on (see DESIGN.md):
// - unchanged: the sent text equals the draft after ignoring whitespace and the signature;
// - edit size: the share of characters changed (Levenshtein distance / longer length).

// Common sign-off lines, in the languages the demo inbox sees
const SIGN_OFF =
  /^(best( regards| wishes)?|kind regards|warm regards|regards|thanks|thank you|many thanks|cheers|sincerely|all the best|viele grüße|mit freundlichen grüßen|saludos|un saludo|cordialement|bien à vous|merci|obrigado|gracias)[,.!]?$/i;

/** The text without its signature: everything from a "-- " line, or from the last sign-off
 *  line near the end ("Best regards,", "Thanks,"), is dropped. */
export function stripSignature(text: string): string {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const delimiter = lines.findIndex((line) => /^--\s?$/.test(line));
  if (delimiter >= 0) return lines.slice(0, delimiter).join("\n");
  // Only the last 6 lines can hold a sign-off, so "Thanks!" opening a reply survives
  for (let index = lines.length - 1; index >= Math.max(0, lines.length - 6); index -= 1) {
    if (SIGN_OFF.test((lines[index] ?? "").trim())) return lines.slice(0, index).join("\n");
  }
  return text;
}

export function normalizeForComparison(text: string): string {
  return stripSignature(text).replace(/\s+/g, " ").trim();
}

export function isUnchanged(draft: string, sent: string): boolean {
  return normalizeForComparison(draft) === normalizeForComparison(sent);
}

/** Edit distance with two rows of the dynamic-programming table: O(n·m) time, O(m) memory. */
export function levenshtein(a: string, b: string): number {
  if (a === b) return 0;
  let previous = Array.from({ length: b.length + 1 }, (_, index) => index);
  for (let i = 1; i <= a.length; i += 1) {
    const current = [i];
    for (let j = 1; j <= b.length; j += 1) {
      const substitution = (previous[j - 1] ?? 0) + (a[i - 1] === b[j - 1] ? 0 : 1);
      current[j] = Math.min((previous[j] ?? 0) + 1, (current[j - 1] ?? 0) + 1, substitution);
    }
    previous = current;
  }
  return previous[b.length] ?? 0;
}

/** 0 = sent as drafted, 1 = completely rewritten. Rounded to 3 decimals. */
export function editRatio(draft: string, sent: string): number {
  const a = normalizeForComparison(draft);
  const b = normalizeForComparison(sent);
  const longer = Math.max(a.length, b.length);
  if (longer === 0) return 0;
  return Math.round((levenshtein(a, b) / longer) * 1000) / 1000;
}
