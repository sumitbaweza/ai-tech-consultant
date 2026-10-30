import type { ReactNode } from "react";
import { FolderNav } from "@/components/folder-nav";
import { TopBar } from "@/components/top-bar";
import { getDb } from "@/db/client";
import { FOLDERS, type Folder } from "@/lib/folders";
import { unreadCounts } from "@/lib/mailbox";
import { getViewer } from "@/lib/viewer";

const NO_UNREAD = Object.fromEntries(FOLDERS.map((folder) => [folder, 0])) as Record<
  Folder,
  number
>;

/** The Gmail-style shell around every signed-in page: top bar, left rail, content. */
export default async function MailLayout({ children }: { children: ReactNode }) {
  const { user, mode, mailbox } = await getViewer();
  const unread = mailbox ? await unreadCounts(getDb(), mailbox.id) : NO_UNREAD;

  return (
    <div className="bg-muted flex h-dvh flex-col">
      <TopBar user={user} mode={mode} />
      <div className="flex min-h-0 flex-1">
        <aside className="w-60 shrink-0 py-2">
          <FolderNav unread={unread} />
        </aside>
        <main className="bg-background mr-4 mb-4 flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-2xl">
          {mailbox ? (
            children
          ) : (
            <p className="text-muted-foreground p-8 text-sm">
              {mode === "fixtures"
                ? "The fixture emails are not loaded yet. Run `pnpm db:setup`, or restart inbox-migrate."
                : "This Gmail account has not been synced yet."}
            </p>
          )}
        </main>
      </div>
    </div>
  );
}
