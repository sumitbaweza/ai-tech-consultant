import { sql } from "drizzle-orm";
import { readMigrationFiles } from "drizzle-orm/migrator";
import type { Db } from "@/db/client";

// Which migrations already ran: the same table, columns and schema drizzle-kit would use
const JOURNAL = sql`inbox.__drizzle_migrations`;

/** Apply the SQL files in drizzle/ that this database hasn't run yet; returns how many ran.
 *
 *  Why not Drizzle's own migrate(): it starts with CREATE SCHEMA IF NOT EXISTS, and Postgres
 *  checks the right to create schemas before it checks whether the schema exists. The inbox
 *  role has no such right, by design (db/init/01-schemas.sh), so that call always fails.
 *  This is the same loop without that line, so drizzle-kit still reads the history. */
export async function runMigrations(db: Db, migrationsFolder = "drizzle"): Promise<number> {
  // Each file's statements, a hash of the file and its timestamp from drizzle/meta/_journal.json
  const migrations = readMigrationFiles({ migrationsFolder });

  await db.execute(
    sql`create table if not exists ${JOURNAL} (id serial primary key, hash text not null, created_at bigint)`,
  );

  return db.transaction(async (tx) => {
    // Two containers starting at once must not both apply the same file
    await tx.execute(sql`lock table ${JOURNAL} in exclusive mode`);
    const [last] = await tx.execute<{ created_at: string }>(
      sql`select created_at from ${JOURNAL} order by created_at desc limit 1`,
    );
    const lastMillis = last === undefined ? -1 : Number(last.created_at);

    let applied = 0;
    for (const migration of migrations) {
      if (migration.folderMillis <= lastMillis) continue;
      for (const statement of migration.sql) await tx.execute(sql.raw(statement));
      await tx.execute(
        sql`insert into ${JOURNAL} (hash, created_at) values (${migration.hash}, ${migration.folderMillis})`,
      );
      applied += 1;
    }
    return applied;
  });
}
