import { signInOptions } from "@/auth";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { formatAgo } from "@/lib/format";
import { getViewer } from "@/lib/viewer";

export default async function SettingsPage() {
  const { user, mode, mailbox } = await getViewer();
  const options = signInOptions();
  const rows: [string, string][] = [
    ["Mode", mode === "fixtures" ? "Fixtures (fixtures/emails.json)" : "Gmail"],
    ["Mailbox", mailbox?.address ?? "Not loaded yet"],
    ["Signed in as", user.email ?? "Unknown"],
    ["Google sign-in", options.google ? "Configured" : "Not configured (GOOGLE_CLIENT_ID is empty)"],
  ];
  if (mailbox?.provider === "google") {
    rows.push(
      ["Last synced", mailbox.lastSyncedAt ? formatAgo(mailbox.lastSyncedAt, new Date()) : "Not yet"],
      ["Sync status", mailbox.needsSignIn ? "Sign in with Google again" : (mailbox.lastSyncError ?? "OK")],
    );
  }

  return (
    <div className="overflow-y-auto p-6">
      <h1 className="text-xl">Settings</h1>
      <Card className="mt-6 max-w-2xl">
        <CardHeader>
          <CardTitle>Account</CardTitle>
          <CardDescription>
            Categories, the auto-send threshold and the voice guide will live here too.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-[10rem_1fr] gap-y-2 text-sm">
            {rows.map(([label, value]) => (
              <div key={label} className="contents">
                <dt className="text-muted-foreground">{label}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
        </CardContent>
      </Card>
    </div>
  );
}
