import { notFound } from "next/navigation";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getDb } from "@/db/client";
import { isFolder } from "@/lib/folders";
import { getEmail } from "@/lib/mailbox";
import { getViewer } from "@/lib/viewer";

/** The reading pane. React escapes the text it renders, so an email body can't inject markup. */
export default async function EmailPage({
  params,
}: {
  params: Promise<{ folder: string; id: string }>;
}) {
  const { folder, id } = await params;
  if (!isFolder(folder) || !/^\d{1,9}$/.test(id)) notFound();
  const { mailbox } = await getViewer();
  if (!mailbox) notFound();
  const email = await getEmail(getDb(), mailbox.id, folder, Number(id));
  if (!email) notFound();

  const sentAt = email.receivedAt.toLocaleString("en-US", {
    timeZone: "UTC",
    dateStyle: "medium",
    timeStyle: "short",
  });

  return (
    <article className="p-6">
      <h2 className="text-xl">{email.subject || "(no subject)"}</h2>
      <div className="mt-4 flex flex-wrap items-start justify-between gap-x-4 gap-y-1 text-sm">
        <div className="min-w-0 break-words">
          <span className="font-medium">{email.fromName}</span>{" "}
          <span className="text-muted-foreground">&lt;{email.fromAddress}&gt;</span>
          <div className="text-muted-foreground">to {email.toAddresses.join(", ")}</div>
        </div>
        <time dateTime={email.receivedAt.toISOString()} className="text-muted-foreground shrink-0">
          {sentAt} UTC
        </time>
      </div>
      <div className="mt-6 text-sm leading-relaxed whitespace-pre-wrap">
        {email.bodyText || "(empty email)"}
      </div>

      {folder === "inbox" && (
        <Card className="mt-8">
          <CardHeader>
            <CardTitle>AI draft</CardTitle>
            <CardDescription>No draft yet: drafting is not switched on in this version.</CardDescription>
          </CardHeader>
        </Card>
      )}
    </article>
  );
}
