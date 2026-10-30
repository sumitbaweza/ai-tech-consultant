// Run by the inbox-migrate Compose service before the app starts (`pnpm db:setup`):
// apply the migrations in drizzle/, then, in fixtures mode, load fixtures/emails.json.
import { connect } from "@/db/client";
import { runMigrations } from "@/db/migrate";
import { getEnv } from "@/env";
import { importFixtures, loadFixtures } from "@/lib/fixtures";
import { loadRootEnv } from "@/root-env";

loadRootEnv();

const db = connect();
try {
  const applied = await runMigrations(db);
  console.log(`Migrations: ${String(applied)} applied`);

  if (getEnv().INBOX_MODE === "fixtures") {
    const result = await importFixtures(db, await loadFixtures());
    console.log(`Fixtures: ${String(result.inserted)} new of ${String(result.total)} emails`);
  }
} finally {
  await db.$client.end();
}
