"use client";

import { useActionState } from "react";
import type { ActionState } from "@/app/actions";
import { Button } from "@/components/ui/button";

/** A button that runs a server action and shows what came back: a message or an error. */
export function ActionButton({
  action,
  label,
  pendingLabel,
  fields = {},
  variant = "default",
}: {
  action: (state: ActionState, form: FormData) => Promise<ActionState>;
  label: string;
  pendingLabel: string;
  fields?: Record<string, string>;
  variant?: "default" | "outline" | "secondary" | "destructive" | "ghost";
}) {
  const [state, run, pending] = useActionState(action, {});
  return (
    <form action={run} className="flex flex-wrap items-center gap-3">
      {Object.entries(fields).map(([name, value]) => (
        <input key={name} type="hidden" name={name} value={value} />
      ))}
      <Button type="submit" size="sm" variant={variant} disabled={pending}>
        {pending ? pendingLabel : label}
      </Button>
      {state.error !== undefined && <span className="text-destructive text-sm">{state.error}</span>}
      {state.message !== undefined && <span className="text-muted-foreground text-sm">{state.message}</span>}
    </form>
  );
}
