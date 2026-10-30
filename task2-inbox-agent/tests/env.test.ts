import { describe, expect, it } from "vitest";
import { parseDbEnv, parseEnv } from "@/env";

const required = { INBOX_DB_PASSWORD: "x", AUTH_SECRET: "a".repeat(32) };

describe("parseEnv", () => {
  it("defaults to fixtures mode on this machine's Postgres", () => {
    const env = parseEnv(required);
    expect(env.INBOX_MODE).toBe("fixtures");
    expect(env.DB_HOST).toBe("127.0.0.1");
    expect(env.DB_PORT).toBe(5432);
  });

  it("treats blank values from Docker Compose as not set", () => {
    const env = parseEnv({ ...required, GOOGLE_CLIENT_ID: "", INBOX_DEMO_PASSWORD: "  " });
    expect(env.GOOGLE_CLIENT_ID).toBeUndefined();
    expect(env.INBOX_DEMO_PASSWORD).toBeUndefined();
  });

  it("refuses a missing password, a short secret or an unknown mode", () => {
    expect(() => parseEnv({ AUTH_SECRET: required.AUTH_SECRET })).toThrow();
    expect(() => parseEnv({ ...required, AUTH_SECRET: "short" })).toThrow();
    expect(() => parseEnv({ ...required, INBOX_MODE: "imap" })).toThrow();
  });
});

describe("parseDbEnv", () => {
  it("needs only the database password, not the sign-in settings", () => {
    expect(parseDbEnv({ INBOX_DB_PASSWORD: "x" }).DB_NAME).toBe("consultant");
  });
});
