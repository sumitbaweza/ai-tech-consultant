import { drizzle } from "drizzle-orm/postgres-js";
import postgres from "postgres";
import { getDbEnv } from "@/env";
import * as schema from "@/db/schema";

/** Open a pool as the `inbox` role. Tests pass "consultant_test" to use the test database. */
export function connect(database?: string) {
  const env = getDbEnv();
  const client = postgres({
    host: env.DB_HOST,
    port: env.DB_PORT,
    database: database ?? env.DB_NAME,
    username: "inbox",
    password: env.INBOX_DB_PASSWORD,
    max: 5,
    // Postgres notices ("already exists, skipping") are not errors: don't print them
    onnotice: () => undefined,
  });
  return drizzle(client, { schema });
}

export type Db = ReturnType<typeof connect>;

// One pool per server process. `next dev` reloads modules on every edit, which would open a
// new pool each time, so the pool lives on globalThis, which survives the reloads.
const globalForDb = globalThis as typeof globalThis & { inboxDb?: Db };

export function getDb(): Db {
  globalForDb.inboxDb ??= connect();
  return globalForDb.inboxDb;
}
