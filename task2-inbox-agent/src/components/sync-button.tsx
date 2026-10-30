"use client";

import { RefreshCw } from "lucide-react";
import { useFormStatus } from "react-dom";
import { syncNowAction } from "@/app/actions";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

function SubmitButton() {
  // True while the form's server action runs, so the icon spins until the sync is done
  const { pending } = useFormStatus();
  return (
    <Button type="submit" variant="ghost" size="icon" aria-label="Sync now" title="Sync now" disabled={pending}>
      <RefreshCw className={cn(pending && "animate-spin")} />
    </Button>
  );
}

/** Syncs the Gmail account now, rather than at the worker's next minute. */
export function SyncButton() {
  return (
    <form action={syncNowAction}>
      <SubmitButton />
    </form>
  );
}
