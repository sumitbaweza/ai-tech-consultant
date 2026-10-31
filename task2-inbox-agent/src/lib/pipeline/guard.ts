// Clean an email before any model sees it, and flag risks with plain patterns. The classifier
// looks for the same risks; this is the second, cheaper net, and the one that still works
// when Claude is down. Every flag here blocks auto-send.
import type { GuardFlag } from "@/lib/domain";

// Long enough for any real support email; past it, the model gets the first part only
export const MAX_MODEL_CHARS = 12_000;

const QUOTE_HEADERS = [
  /^On .{5,200} wrote:\s*$/, // Gmail, Apple Mail: "On Thu, Oct 30, 2026 at 8:40 AM Name <a@b> wrote:"
  /^-{2,}\s*Original Message\s*-{2,}$/i, // Outlook
  /^From: .+$/, // Outlook's reply header block starts with From:
];
const SIGNATURE_DELIMITER = /^--\s?$/; // the standard "-- " line before a signature
const MOBILE_FOOTER = /^Sent from my (iPhone|iPad|Android|mobile)/i;

const INJECTION = [
  /\b(ignore|disregard|forget)\b.{0,30}\b(instructions|guidelines|rules|prompt)\b/i,
  /\byou are now\b/i,
  /\b(system|developer) (message|prompt)\s*:/i,
  /\badmin mode\b/i,
  /\bnote to the (ai|assistant|bot)\b/i,
  /\b(assistant|ai) must\b/i,
  /\bpre-?approved by\b/i,
];
const PERSONAL_DATA = [
  /\b(?:\d[ -]?){13,19}\b/, // a card number, with or without spaces
  /\b(cvv|cvc|security code)\b/i,
  /\bpassword\s*(is|:|=)/i,
  /\bseed phrase\b/i,
];
const LEGAL = [/\b(lawyer|attorney|solicitor|lawsuit|legal (action|proceedings)|sue you|dispute the charge|chargeback)\b/i];

export type GuardResult = { text: string; flags: GuardFlag[] };

/** The part of the email the customer just wrote: no quoted history, signature or footer. */
export function cleanEmailText(body: string): string {
  const kept: string[] = [];
  for (const line of body.replace(/\r\n/g, "\n").split("\n")) {
    const trimmed = line.trim();
    if (QUOTE_HEADERS.some((pattern) => pattern.test(trimmed))) break;
    if (SIGNATURE_DELIMITER.test(line) || MOBILE_FOOTER.test(trimmed)) break;
    if (trimmed.startsWith(">")) continue; // a quoted line without a header above it
    kept.push(line);
  }
  return kept.join("\n").replace(/\n{3,}/g, "\n\n").trim();
}

export function guardEmail(subject: string, body: string): GuardResult {
  const flags: GuardFlag[] = [];
  let text = cleanEmailText(body);

  if (text.length < 3) flags.push("empty");
  if (text.length > MAX_MODEL_CHARS) {
    flags.push("huge");
    text = `${text.slice(0, MAX_MODEL_CHARS)}\n[The rest of this email was cut: it is longer than ${String(MAX_MODEL_CHARS)} characters.]`;
  }
  // Patterns run on everything the customer sent, quoted parts and hidden comments included
  const everything = `${subject}\n${body}`;
  if (INJECTION.some((pattern) => pattern.test(everything))) flags.push("injection_pattern");
  if (PERSONAL_DATA.some((pattern) => pattern.test(everything))) flags.push("personal_data_pattern");
  if (LEGAL.some((pattern) => pattern.test(everything))) flags.push("legal_pattern");
  return { text, flags };
}
