// The Settings screen's knobs and the product-area list, with the defaults setup writes once.
import { asc, eq, sql } from "drizzle-orm";
import type { Db } from "@/db/client";
import { productAreas, settings } from "@/db/schema";
import { DEFAULT_PRODUCT_AREAS } from "@/lib/domain";

export const DEFAULT_VOICE_GUIDE = `Write as the Support Team of a directory-website platform (Brilliant Directories).
- Warm, direct and practical. Open with the answer, not with thanks for reaching out.
- Give exact steps with the admin menu path in bold, e.g. **Design Settings > Logo**.
- Short paragraphs; numbered steps for anything with more than two steps.
- Use only the facts you are given. When something is unknown, say a teammate will follow up; never guess a setting or a price.
- Reply in the customer's language.
- No promises about refunds, credits, legal matters or deadlines: say the team will review and reply.
- Sign off with: Best regards,\\nThe Support Team`;

export type InboxSettings = typeof settings.$inferSelect;

/** Write the default settings row and product areas if they're missing. Run by `pnpm db:setup`
 *  on every start, and harmless to repeat: it never overwrites what someone changed. */
export async function seedDefaults(db: Db): Promise<void> {
  await db.insert(settings).values({ id: 1, voiceGuide: DEFAULT_VOICE_GUIDE }).onConflictDoNothing();
  await db
    .insert(productAreas)
    .values(DEFAULT_PRODUCT_AREAS.map((name) => ({ name })))
    .onConflictDoNothing();
}

export async function getSettings(db: Db): Promise<InboxSettings> {
  const [row] = await db.select().from(settings).where(eq(settings.id, 1));
  if (row === undefined) throw new Error("The settings row is missing: run `pnpm db:setup`");
  return row;
}

export async function updateSettings(
  db: Db,
  values: Pick<InboxSettings, "autoSendAfter" | "sampleEvery" | "undoMinutes" | "voiceGuide">,
): Promise<void> {
  await db.update(settings).set({ ...values, updatedAt: sql`now()` }).where(eq(settings.id, 1));
}

export async function listProductAreas(db: Db, onlyActive = false) {
  const rows = await db.select().from(productAreas).orderBy(asc(productAreas.id));
  return onlyActive ? rows.filter((row) => row.active) : rows;
}
