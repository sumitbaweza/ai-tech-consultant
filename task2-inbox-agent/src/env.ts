import { z } from "zod";

// Docker Compose passes `${NAME:-}` as an empty string when the .env file leaves it blank,
// so a blank value counts as "not set".
const optional = z
  .string()
  .optional()
  .transform((value) => (value === undefined || value.trim() === "" ? undefined : value));

// What the database connection needs, and nothing more, so tests and scripts that only touch
// the database don't need the sign-in settings.
const DbEnvSchema = z.object({
  DB_HOST: z.string().default("127.0.0.1"),
  DB_PORT: z.coerce.number().int().default(5432),
  DB_NAME: z.string().default("consultant"),
  INBOX_DB_PASSWORD: z.string().min(1, "Set INBOX_DB_PASSWORD in .env"),
});

const EnvSchema = DbEnvSchema.extend({
  // Auth.js signs the session cookie with it. Checked here so a missing one fails the inbox
  // with a clear message, while Compose still starts the other services.
  AUTH_SECRET: z
    .string()
    .min(32, "Set AUTH_SECRET in .env: node -e \"console.log(require('crypto').randomBytes(32).toString('base64'))\""),

  // fixtures: emails come from fixtures/emails.json and a demo password signs you in.
  // gmail: emails come from the Gmail account you sign in with through Google.
  INBOX_MODE: z.enum(["fixtures", "gmail"]).default("fixtures"),
  INBOX_DEMO_PASSWORD: optional,
  GOOGLE_CLIENT_ID: optional,
  GOOGLE_CLIENT_SECRET: optional,

  // May be empty: the inbox still shows mail, and drafting says the key is missing
  ANTHROPIC_API_KEY: optional,
  // The Knowledge Brain's API (Task 1), reached over HTTP only, never by importing its code.
  // The key is one of the Brain's BRAIN_API_KEYS. Empty: facts are marked unavailable.
  BRAIN_API_URL: z.url().default("http://127.0.0.1:8000"),
  BRAIN_API_KEY: optional,
});

export type DbEnv = z.infer<typeof DbEnvSchema>;
export type Env = z.infer<typeof EnvSchema>;

export function parseEnv(source: Record<string, string | undefined>): Env {
  return EnvSchema.parse(source);
}

export function parseDbEnv(source: Record<string, string | undefined>): DbEnv {
  return DbEnvSchema.parse(source);
}

// Read and check the environment on first use, not at import, so `next build` needs no secrets
let cachedEnv: Env | undefined;
let cachedDbEnv: DbEnv | undefined;

export function getEnv(): Env {
  cachedEnv ??= parseEnv(process.env);
  return cachedEnv;
}

export function getDbEnv(): DbEnv {
  cachedDbEnv ??= parseDbEnv(process.env);
  return cachedDbEnv;
}
