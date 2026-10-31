"use client";

import {
  BotMessageSquare,
  ClipboardCheck,
  File,
  GraduationCap,
  Inbox,
  type LucideIcon,
  OctagonAlert,
  Send,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { type Folder, FOLDERS, LIST_LABELS, type MailList, VIEWS } from "@/lib/folders";
import { cn } from "@/lib/utils";

const ICONS: Record<MailList, LucideIcon> = {
  inbox: Inbox,
  drafts: File,
  sent: Send,
  spam: OctagonAlert,
  trash: Trash2,
  "needs-review": ClipboardCheck,
  "auto-sent": BotMessageSquare,
};

function RailLink({
  href,
  icon: Icon,
  label,
  count,
  active,
}: {
  href: "/learning" | `/mail/${MailList}`;
  icon: LucideIcon;
  label: string;
  count: number;
  active: boolean;
}) {
  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex items-center gap-4 rounded-r-full py-1.5 pr-4 pl-6 text-sm",
        active ? "bg-primary/15 font-semibold" : "hover:bg-background/60",
      )}
    >
      <Icon className="size-4" aria-hidden />
      <span className="flex-1">{label}</span>
      {count > 0 && <span className="text-xs">{count}</span>}
    </Link>
  );
}

/** Gmail's left rail: the five folders where Gmail has them, then the agent's own views.
 *  A client component because it highlights the list in the URL. */
export function FolderNav({ unread, needsReview }: { unread: Record<Folder, number>; needsReview: number }) {
  const pathname = usePathname();
  return (
    <nav aria-label="Folders" className="flex flex-col gap-4">
      <ul>
        {FOLDERS.map((folder) => (
          <li key={folder}>
            <RailLink
              href={`/mail/${folder}`}
              icon={ICONS[folder]}
              label={LIST_LABELS[folder]}
              count={unread[folder]}
              active={pathname.startsWith(`/mail/${folder}`)}
            />
          </li>
        ))}
      </ul>
      <div>
        <p className="text-muted-foreground px-6 pb-1 text-xs font-medium tracking-wide uppercase">Agent</p>
        <ul>
          {VIEWS.map((view) => (
            <li key={view}>
              <RailLink
                href={`/mail/${view}`}
                icon={ICONS[view]}
                label={LIST_LABELS[view]}
                count={view === "needs-review" ? needsReview : 0}
                active={pathname.startsWith(`/mail/${view}`)}
              />
            </li>
          ))}
          <li>
            <RailLink
              href="/learning"
              icon={GraduationCap}
              label="Learning"
              count={0}
              active={pathname.startsWith("/learning")}
            />
          </li>
        </ul>
      </div>
    </nav>
  );
}
