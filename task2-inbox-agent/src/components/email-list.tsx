"use client";

import Link from "next/link";
import { useSelectedLayoutSegment } from "next/navigation";
import { Badge } from "@/components/ui/badge";
import { INTENT_LABELS, type Intent } from "@/lib/domain";
import { LIST_LABELS, type MailList } from "@/lib/folders";
import { cn } from "@/lib/utils";

export type EmailListItem = {
  id: number;
  fromName: string;
  subject: string;
  snippet: string;
  date: string;
  isRead: boolean;
  intent: string | null;
  productArea: string | null;
  risky: boolean;
  draftStatus: string | null;
};

const DRAFT_BADGES: Record<string, { label: string; className: string }> = {
  ready: { label: "Draft ready", className: "bg-blue-100 text-blue-900" },
  scheduled: { label: "Auto-send pending", className: "bg-amber-100 text-amber-900" },
  sent: { label: "Replied", className: "bg-green-100 text-green-900" },
  auto_sent: { label: "Auto-sent", className: "bg-green-100 text-green-900" },
  failed: { label: "Send failed", className: "bg-red-100 text-red-900" },
  discarded: { label: "Discarded", className: "bg-muted text-muted-foreground" },
};

/** The middle column. A client component because it highlights the email that is open. */
export function EmailList({ list, items }: { list: MailList; items: EmailListItem[] }) {
  // The [id] segment under this list's layout: the open email's id, or null
  const openId = useSelectedLayoutSegment();

  if (items.length === 0) {
    return <p className="text-muted-foreground px-4 py-8 text-sm">Nothing in {LIST_LABELS[list]}.</p>;
  }
  return (
    <ul>
      {items.map((item) => {
        const badge = item.draftStatus === null ? undefined : DRAFT_BADGES[item.draftStatus];
        return (
          <li key={item.id}>
            <Link
              href={`/mail/${list}/${String(item.id)}`}
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
              {(item.intent !== null || badge !== undefined) && (
                <div className="mt-1.5 flex flex-wrap gap-1 font-normal">
                  {item.intent !== null && (
                    <Badge variant="outline" className="text-[11px]">
                      {INTENT_LABELS[item.intent as Intent]} · {item.productArea}
                    </Badge>
                  )}
                  {item.risky && (
                    <Badge variant="outline" className="border-red-300 text-[11px] text-red-700">
                      Risk
                    </Badge>
                  )}
                  {badge !== undefined && (
                    <span className={cn("rounded-md px-1.5 py-0.5 text-[11px]", badge.className)}>{badge.label}</span>
                  )}
                </div>
              )}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
