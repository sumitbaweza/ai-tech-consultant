import { removeRuleAction, rollbackAction } from "@/app/actions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getDb } from "@/db/client";
import { categoryLabel } from "@/lib/domain";
import { formatAgo } from "@/lib/format";
import { learningOverview } from "@/lib/learning-view";
import { getSettings } from "@/lib/settings";

const STATE_BADGE: Record<string, { label: string; className: string }> = {
  manual: { label: "Manual review", className: "bg-muted text-foreground" },
  earning: { label: "Earning", className: "bg-blue-100 text-blue-900" },
  auto: { label: "Auto-send", className: "bg-green-100 text-green-900" },
};

function percent(value: number | null): string {
  return value === null ? "–" : `${String(Math.round(value * 100))}%`;
}

/** Each category's way to auto-send, its playbook versions with remove and rollback, and the
 *  edit-size trend that shows whether learning helps. */
export default async function LearningPage() {
  const db = getDb();
  const [{ categories, trend, events }, settings] = await Promise.all([learningOverview(db), getSettings(db)]);
  const now = new Date();

  return (
    <div className="flex flex-col gap-6 overflow-y-auto p-6">
      <div>
        <h1 className="text-xl">Learning</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          A category is an intent × product area pair. {settings.autoSendAfter} unedited sends in a row turn on
          auto-send; any edit or discard restarts the count. Refunds, legal threats, angry customers, personal data,
          prompt injection and &quot;don&apos;t know&quot; answers never auto-send.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Edit size over time</CardTitle>
          <CardDescription>
            Share of characters a human changed, per send, oldest on the left. Falling bars mean the drafts need less
            fixing.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {trend.length === 0 ? (
            <p className="text-muted-foreground text-sm">No sends yet.</p>
          ) : (
            <div className="flex h-24 items-end gap-1" role="img" aria-label="Edit size per send">
              {trend.map((point, index) => (
                <div
                  key={`${point.categoryKey}-${String(index)}`}
                  title={`${categoryLabel(point.categoryKey)}: ${percent(point.editRatio)} changed`}
                  className="bg-primary/70 w-3 rounded-t"
                  style={{ height: `${String(Math.max(4, point.editRatio * 100))}%` }}
                />
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {categories.length === 0 && (
        <p className="text-muted-foreground text-sm">No categories yet: draft and send some replies first.</p>
      )}

      {categories.map((category) => {
        const badge = STATE_BADGE[category.state] ?? { label: category.state, className: "bg-muted text-foreground" };
        const active = category.versions.find((version) => version.version === category.activeVersion);
        return (
          <Card key={category.key}>
            <CardHeader>
              <CardTitle className="flex flex-wrap items-center gap-2">
                {categoryLabel(category.key)}
                <span className={`rounded-md px-2 py-0.5 text-xs ${badge.className}`}>{badge.label}</span>
                <Badge variant="outline">
                  {category.streak} / {settings.autoSendAfter} unedited in a row
                </Badge>
              </CardTitle>
              <CardDescription>
                {category.humanSends} sent by a human ({category.uneditedSends} unedited), {category.autoSends}{" "}
                auto-sent. Recent edit size {percent(category.recentEditSize)}.
                {category.lastReason !== null && <> Last change: {category.lastReason}.</>}
              </CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-4 text-sm">
              <section>
                <h3 className="mb-2 font-semibold">
                  Active playbook: {category.activeVersion === 0 ? "none (no rules)" : `v${String(category.activeVersion)}`}
                </h3>
                {active === undefined || active.rules.length === 0 ? (
                  <p className="text-muted-foreground">No rules yet. Edits to drafts in this category teach new ones.</p>
                ) : (
                  <ol className="flex list-decimal flex-col gap-2 pl-5">
                    {active.rules.map((rule) => (
                      <li key={rule.text}>
                        <div className="flex flex-wrap items-start justify-between gap-2">
                          <div>
                            <p>{rule.text}</p>
                            <p className="text-muted-foreground text-xs">
                              {rule.why}
                              {rule.sourceDraftId !== null && <> (from draft {rule.sourceDraftId})</>}
                            </p>
                          </div>
                          <form action={removeRuleAction}>
                            <input type="hidden" name="categoryKey" value={category.key} />
                            <input type="hidden" name="rule" value={rule.text} />
                            <Button type="submit" size="sm" variant="ghost">
                              Remove
                            </Button>
                          </form>
                        </div>
                      </li>
                    ))}
                  </ol>
                )}
              </section>

              {category.versions.length > 0 && (
                <details>
                  <summary className="cursor-pointer font-semibold">All versions ({category.versions.length})</summary>
                  <ul className="mt-2 flex flex-col gap-2">
                    {[...category.versions, { version: 0, note: "No rules", createdAt: null, rules: [] }].map((version) => (
                      <li key={version.version} className="flex flex-wrap items-center justify-between gap-2 border-b pb-2">
                        <span>
                          <strong>v{version.version}</strong> · {version.rules.length} rule(s) · {version.note}
                          {version.createdAt !== null && <> · {formatAgo(version.createdAt, now)}</>}
                        </span>
                        {version.version === category.activeVersion ? (
                          <Badge>Active</Badge>
                        ) : (
                          <form action={rollbackAction}>
                            <input type="hidden" name="categoryKey" value={category.key} />
                            <input type="hidden" name="version" value={version.version} />
                            <Button type="submit" size="sm" variant="outline">
                              Use this version
                            </Button>
                          </form>
                        )}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </CardContent>
          </Card>
        );
      })}

      {events.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Why autonomy changed</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="flex flex-col gap-1 text-sm">
              {events.map((event) => (
                <li key={event.id}>
                  <span className="text-muted-foreground">{formatAgo(event.createdAt, now)}</span> ·{" "}
                  {categoryLabel(event.categoryKey)} · <strong>{event.kind}</strong>: {event.detail}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
