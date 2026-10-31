// What the Learning screen shows: each category's way to auto-send, its playbook versions,
// and whether edits are getting smaller (the proof the learning works, or doesn't).
import { and, asc, desc, eq, inArray, isNotNull } from "drizzle-orm";
import type { Db } from "@/db/client";
import { autonomyEvents, categoryState, drafts, playbooks } from "@/db/schema";

export type CategoryOverview = {
  key: string;
  state: string;
  streak: number;
  activeVersion: number;
  lastReason: string | null;
  humanSends: number;
  uneditedSends: number;
  autoSends: number;
  // Average edit size of the last 10 human sends, 0 to 1
  recentEditSize: number | null;
  versions: { version: number; note: string; createdAt: Date; rules: { text: string; why: string; sourceDraftId: number | null }[] }[];
};

export async function learningOverview(db: Db) {
  const [categories, sent, versions, events] = await Promise.all([
    db.select().from(categoryState).orderBy(desc(categoryState.updatedAt)),
    db
      .select({
        categoryKey: drafts.categoryKey,
        sentBy: drafts.sentBy,
        edited: drafts.edited,
        editRatio: drafts.editRatio,
        sentAt: drafts.sentAt,
      })
      .from(drafts)
      .where(and(inArray(drafts.status, ["sent", "auto_sent"]), isNotNull(drafts.sentAt)))
      .orderBy(asc(drafts.sentAt)),
    db.select().from(playbooks).orderBy(desc(playbooks.version)),
    db.select().from(autonomyEvents).orderBy(desc(autonomyEvents.createdAt)).limit(25),
  ]);

  const overview: CategoryOverview[] = categories.map((category) => {
    const mine = sent.filter((row) => row.categoryKey === category.categoryKey);
    const human = mine.filter((row) => row.sentBy === "human");
    const recent = human.slice(-10);
    return {
      key: category.categoryKey,
      state: category.state,
      streak: category.uneditedStreak,
      activeVersion: category.activePlaybookVersion,
      lastReason: category.lastReason,
      humanSends: human.length,
      uneditedSends: human.filter((row) => row.edited === false).length,
      autoSends: mine.filter((row) => row.sentBy === "auto").length,
      recentEditSize:
        recent.length === 0 ? null : recent.reduce((sum, row) => sum + (row.editRatio ?? 0), 0) / recent.length,
      versions: versions
        .filter((row) => row.categoryKey === category.categoryKey)
        .map((row) => ({ version: row.version, note: row.note, createdAt: row.createdAt, rules: row.rules })),
    };
  });

  // Every human send's edit size in order: the trend line across all categories
  const trend = sent
    .filter((row) => row.sentBy === "human")
    .map((row) => ({ categoryKey: row.categoryKey, editRatio: row.editRatio ?? 0 }));
  return { categories: overview, trend: trend.slice(-40), events };
}

export async function categoryEvents(db: Db, key: string) {
  return db
    .select()
    .from(autonomyEvents)
    .where(eq(autonomyEvents.categoryKey, key))
    .orderBy(desc(autonomyEvents.createdAt))
    .limit(10);
}
