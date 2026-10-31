// Run by the inbox-migrate Compose service before the app starts (`pnpm db:setup`):
// apply the migrations in drizzle/, write the default settings, then load fixtures/emails.json.
// The fixtures load in both modes (evals and tests use them); only fixtures mode shows them.
import { connect } from "@/db/client";
import { runMigrations } from "@/db/migrate";
import { importFixtures, loadFixtures } from "@/lib/fixtures";
import { seedDefaults } from "@/lib/settings";
import { loadRootEnv } from "@/root-env";

loadRootEnv();

const db = connect();
try {
  const applied = await runMigrations(db);
  console.log(`Migrations: ${String(applied)} applied`);
  await seedDefaults(db);

  const result = await importFixtures(db, await loadFixtures());
  console.log(`Fixtures: ${String(result.inserted)} new of ${String(result.total)} emails`);
} finally {
  await db.$client.end();
}
