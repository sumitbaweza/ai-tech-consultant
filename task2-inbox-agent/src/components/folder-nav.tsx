"use client";

import { File, Inbox, type LucideIcon, OctagonAlert, Send, Trash2 } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { FOLDER_LABELS, FOLDERS, type Folder } from "@/lib/folders";
import { cn } from "@/lib/utils";

const ICONS: Record<Folder, LucideIcon> = {
  inbox: Inbox,
  drafts: File,
  sent: Send,
  spam: OctagonAlert,
  trash: Trash2,
};

/** Gmail's left rail. A client component because it highlights the folder in the URL. */
export function FolderNav({ unread }: { unread: Record<Folder, number> }) {
  const pathname = usePathname();
  return (
    <nav aria-label="Folders">
      <ul>
        {FOLDERS.map((folder) => {
          const Icon = ICONS[folder];
          const active = pathname.startsWith(`/mail/${folder}`);
          return (
            <li key={folder}>
              <Link
                href={`/mail/${folder}`}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex items-center gap-4 rounded-r-full py-1.5 pr-4 pl-6 text-sm",
                  active ? "bg-primary/15 font-semibold" : "hover:bg-background/60",
                )}
              >
                <Icon className="size-4" aria-hidden />
                <span className="flex-1">{FOLDER_LABELS[folder]}</span>
                {unread[folder] > 0 && <span className="text-xs">{unread[folder]}</span>}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
