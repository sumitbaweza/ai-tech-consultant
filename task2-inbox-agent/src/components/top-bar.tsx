import { Mail, Settings } from "lucide-react";
import Link from "next/link";
import { AccountMenu } from "@/components/account-menu";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { Env } from "@/env";

/** Gmail's top bar: the logo on the left, settings and the profile on the right. */
export function TopBar({
  user,
  mode,
}: {
  user: { name?: string | null; email?: string | null };
  mode: Env["INBOX_MODE"];
}) {
  return (
    <header className="flex h-16 shrink-0 items-center gap-3 px-4">
      <Link href="/mail/inbox" className="flex items-center gap-2 pl-2 text-xl">
        <Mail className="text-primary size-6" aria-hidden />
        Inbox Agent
      </Link>
      <div className="flex-1" />
      <Badge variant="secondary">{mode === "fixtures" ? "Fixtures mode" : "Gmail"}</Badge>
      <Button asChild variant="ghost" size="icon" aria-label="Settings">
        <Link href="/settings">
          <Settings />
        </Link>
      </Button>
      <AccountMenu name={user.name} email={user.email} />
    </header>
  );
}
