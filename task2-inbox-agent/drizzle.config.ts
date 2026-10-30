import { defineConfig } from "drizzle-kit";

// `pnpm db:generate` compares src/db/schema.ts with the last migration and writes the next
// SQL file into drizzle/. It needs no database; `pnpm db:setup` applies the files.
export default defineConfig({
  dialect: "postgresql",
  schema: "./src/db/schema.ts",
  out: "./drizzle",
  schemaFilter: ["inbox"],
  // The inbox role may only create objects in its own schema, so the journal lives there too
  migrations: { schema: "inbox", table: "__drizzle_migrations" },
});
