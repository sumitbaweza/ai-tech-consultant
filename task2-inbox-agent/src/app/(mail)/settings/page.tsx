import { toggleProductAreaAction } from "@/app/actions";
import { signInOptions } from "@/auth";
import { AddAreaForm, AutonomyForm } from "@/components/settings-forms";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getDb } from "@/db/client";
import { getEnv } from "@/env";
import { OTHER_AREA } from "@/lib/domain";
import { formatAgo } from "@/lib/format";
import { getSettings, listProductAreas } from "@/lib/settings";
import { getViewer } from "@/lib/viewer";

export default async function SettingsPage() {
  const { user, mode, mailbox } = await getViewer();
  const db = getDb();
  const [settings, areas] = await Promise.all([getSettings(db), listProductAreas(db)]);
  const env = getEnv();
  const options = signInOptions();
  const rows: [string, string][] = [
    ["Mode", mode === "fixtures" ? "Fixtures (fixtures/emails.json)" : "Gmail"],
    ["Mailbox", mailbox?.address ?? "Not loaded yet"],
    ["Signed in as", user.email ?? "Unknown"],
    ["Google sign-in", options.google ? "Configured" : "Not configured (GOOGLE_CLIENT_ID is empty)"],
    ["Claude", env.ANTHROPIC_API_KEY === undefined ? "No ANTHROPIC_API_KEY: drafting is off" : "Configured"],
    ["Knowledge Brain", env.BRAIN_API_KEY === undefined ? "No BRAIN_API_KEY: facts are unavailable" : env.BRAIN_API_URL],
  ];
  if (mailbox?.provider === "google") {
    rows.push(
      ["Last synced", mailbox.lastSyncedAt ? formatAgo(mailbox.lastSyncedAt, new Date()) : "Not yet"],
      ["Sync status", mailbox.needsSignIn ? "Sign in with Google again" : (mailbox.lastSyncError ?? "OK")],
    );
  }

  return (
    <div className="flex flex-col gap-6 overflow-y-auto p-6">
      <h1 className="text-xl">Settings</h1>

      <Card className="max-w-3xl">
        <CardHeader>
          <CardTitle>Categories</CardTitle>
          <CardDescription>
            The product areas every email is sorted into. A draft&apos;s category is its intent × product area, such
            as &quot;How-to × Header&quot;; each one earns auto-send on its own. Emails that fit none go to{" "}
            {OTHER_AREA}.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <ul className="flex flex-wrap gap-2">
            {areas.map((area) => (
              <li key={area.id}>
                <form action={toggleProductAreaAction}>
                  <input type="hidden" name="id" value={area.id} />
                  <input type="hidden" name="active" value={String(!area.active)} />
                  <Button
                    type="submit"
                    size="sm"
                    variant={area.active ? "secondary" : "outline"}
                    title={area.active ? "Click to switch off" : "Click to switch on"}
                    className={area.active ? "" : "text-muted-foreground line-through"}
                  >
                    {area.name}
                  </Button>
                </form>
              </li>
            ))}
          </ul>
          <AddAreaForm />
        </CardContent>
      </Card>

      <Card className="max-w-3xl">
        <CardHeader>
          <CardTitle>Auto-send and voice</CardTitle>
          <CardDescription>
            A category auto-sends after X unedited sends in a row, behind an undo window, with 1 in N auto-sends picked
            for a spot check.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <AutonomyForm
            values={{
              autoSendAfter: settings.autoSendAfter,
              sampleEvery: settings.sampleEvery,
              undoMinutes: settings.undoMinutes,
              voiceGuide: settings.voiceGuide,
            }}
          />
        </CardContent>
      </Card>

      <Card className="max-w-3xl">
        <CardHeader>
          <CardTitle>Account</CardTitle>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-[10rem_1fr] gap-y-2 text-sm">
            {rows.map(([label, value]) => (
              <div key={label} className="contents">
                <dt className="text-muted-foreground">{label}</dt>
                <dd className="break-words">{value}</dd>
              </div>
            ))}
          </dl>
        </CardContent>
      </Card>
    </div>
  );
}
