import { createHash, timingSafeEqual } from "node:crypto";

/** Check the fixtures-mode demo password (INBOX_DEMO_PASSWORD) without leaking, through
 *  timing, how much of a guess was right. timingSafeEqual needs two buffers of the same
 *  length, so both sides are hashed first. */
export function checkDemoPassword(given: string, expected: string | undefined): boolean {
  if (expected === undefined) return false;
  const givenHash = createHash("sha256").update(given).digest();
  const expectedHash = createHash("sha256").update(expected).digest();
  return timingSafeEqual(givenHash, expectedHash);
}
