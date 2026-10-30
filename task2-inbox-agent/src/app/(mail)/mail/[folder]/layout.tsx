import { notFound } from "next/navigation";
import type { ReactNode } from "react";
import { EmailList } from "@/components/email-list";
import { getDb } from "@/db/client";
import { formatListDate } from "@/lib/format";
import { FOLDER_LABELS, isFolder } from "@/lib/folders";
import { listEmails } from "@/lib/mailbox";
import { getViewer } from "@/lib/viewer";

/** One folder: the email list on the left, the open email (the child page) on the right. */
export default async function FolderLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ folder: string }>;
}) {
  const { folder } = await params;
  if (!isFolder(folder)) notFound();
  const { mailbox } = await getViewer();
  if (!mailbox) notFound();

  const now = new Date();
  const rows = await listEmails(getDb(), mailbox.id, folder);
  // Dates are formatted here, on the server, so the client gets plain strings
  const items = rows.map((row) => ({ ...row, date: formatListDate(row.receivedAt, now) }));

  return (
    <div className="flex min-h-0 flex-1">
      <section className="w-2/5 max-w-md min-w-64 shrink-0 overflow-y-auto border-r">
        <h1 className="border-b px-4 py-3 text-sm font-medium">{FOLDER_LABELS[folder]}</h1>
        <EmailList folder={folder} items={items} />
      </section>
      <section className="min-w-0 flex-1 overflow-y-auto">{children}</section>
    </div>
  );
}
