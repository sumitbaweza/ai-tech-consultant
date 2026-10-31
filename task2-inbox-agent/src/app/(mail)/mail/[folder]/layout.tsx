import { notFound } from "next/navigation";
import type { ReactNode } from "react";
import { processBatchAction } from "@/app/actions";
import { ActionButton } from "@/components/action-button";
import { EmailList } from "@/components/email-list";
import { getDb } from "@/db/client";
import { formatListDate } from "@/lib/format";
import { isMailList, LIST_LABELS } from "@/lib/folders";
import { listEmails } from "@/lib/mailbox";
import { getViewer } from "@/lib/viewer";

/** One folder or view: the email list on the left, the open email (the child page) on the right. */
export default async function ListLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ folder: string }>;
}) {
  const { folder: list } = await params;
  if (!isMailList(list)) notFound();
  const { mailbox } = await getViewer();
  if (!mailbox) notFound();

  const now = new Date();
  const rows = await listEmails(getDb(), mailbox.id, list);
  // Dates are formatted here, on the server, so the client gets plain strings
  const items = rows.map((row) => ({
    id: row.id,
    fromName: row.fromName,
    subject: row.subject,
    snippet: row.snippet,
    date: formatListDate(row.receivedAt, now),
    isRead: row.isRead,
    intent: row.intent,
    productArea: row.productArea,
    risky: (row.riskFlags ?? []).length > 0,
    draftStatus: row.draftStatus,
  }));

  return (
    <div className="flex min-h-0 flex-1">
      <section className="w-2/5 max-w-md min-w-64 shrink-0 overflow-y-auto border-r">
        <div className="flex items-center justify-between gap-2 border-b px-4 py-2">
          <h1 className="text-sm font-medium">{LIST_LABELS[list]}</h1>
          {list === "inbox" && (
            <ActionButton
              action={processBatchAction}
              label="Draft next 5"
              pendingLabel="Drafting..."
              variant="outline"
            />
          )}
        </div>
        <EmailList list={list} items={items} />
      </section>
      <section className="min-w-0 flex-1 overflow-y-auto">{children}</section>
    </div>
  );
}
