"use client";

import Link from "next/link";
import { useSelectedLayoutSegment } from "next/navigation";
import { FOLDER_LABELS, type Folder } from "@/lib/folders";
import { cn } from "@/lib/utils";

export type EmailListItem = {
  id: number;
  fromName: string;
  subject: string;
  snippet: string;
  date: string;
  isRead: boolean;
};

/** The middle column. A client component because it highlights the email that is open. */
export function EmailList({ folder, items }: { folder: Folder; items: EmailListItem[] }) {
  // The [id] segment under this folder's layout: the open email's id, or null
  const openId = useSelectedLayoutSegment();

  if (items.length === 0) {
    return (
      <p className="text-muted-foreground px-4 py-8 text-sm">
        Nothing in {FOLDER_LABELS[folder]}.
      </p>
    );
  }
  return (
    <ul>
      {items.map((item) => (
        <li key={item.id}>
          <Link
            href={`/mail/${folder}/${String(item.id)}`}
            className={cn(
              "hover:bg-muted block border-b px-4 py-3",
              openId === String(item.id) && "bg-muted",
              !item.isRead && "font-semibold",
            )}
          >
            <div className="flex items-baseline justify-between gap-2">
              <span className="truncate">{item.fromName}</span>
              <span className="text-muted-foreground shrink-0 text-xs">{item.date}</span>
            </div>
            <div className="truncate text-sm">{item.subject || "(no subject)"}</div>
            <div className="text-muted-foreground truncate text-sm font-normal">{item.snippet}</div>
          </Link>
        </li>
      ))}
    </ul>
  );
}
