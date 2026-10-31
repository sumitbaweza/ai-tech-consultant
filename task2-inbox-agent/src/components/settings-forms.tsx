"use client";

import { useActionState } from "react";
import { type ActionState, addProductAreaAction, saveSettingsAction } from "@/app/actions";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

function Feedback({ state }: { state: ActionState }) {
  if (state.error !== undefined) return <span className="text-destructive text-sm">{state.error}</span>;
  if (state.message !== undefined) return <span className="text-muted-foreground text-sm">{state.message}</span>;
  return null;
}

export function AutonomyForm({
  values,
}: {
  values: { autoSendAfter: number; sampleEvery: number; undoMinutes: number; voiceGuide: string };
}) {
  const [state, save, pending] = useActionState<ActionState, FormData>(saveSettingsAction, {});
  return (
    <form action={save} className="flex flex-col gap-4">
      <div className="grid gap-4 sm:grid-cols-3">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="autoSendAfter">Unedited sends before auto-send (X)</Label>
          <Input id="autoSendAfter" name="autoSendAfter" type="number" min={1} defaultValue={values.autoSendAfter} />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="sampleEvery">Spot-check 1 in N auto-sends</Label>
          <Input id="sampleEvery" name="sampleEvery" type="number" min={1} defaultValue={values.sampleEvery} />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="undoMinutes">Undo window (minutes)</Label>
          <Input id="undoMinutes" name="undoMinutes" type="number" min={1} defaultValue={values.undoMinutes} />
        </div>
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="voiceGuide">Voice guide: how every draft should sound</Label>
        <textarea
          id="voiceGuide"
          name="voiceGuide"
          rows={10}
          defaultValue={values.voiceGuide}
          className="border-input focus-visible:ring-ring/50 w-full rounded-lg border bg-transparent px-3 py-2 text-sm outline-none focus-visible:ring-3"
        />
      </div>
      <div className="flex items-center gap-3">
        <Button type="submit" disabled={pending}>
          {pending ? "Saving..." : "Save"}
        </Button>
        <Feedback state={state} />
      </div>
    </form>
  );
}

export function AddAreaForm() {
  const [state, add, pending] = useActionState<ActionState, FormData>(addProductAreaAction, {});
  return (
    <form action={add} className="flex flex-wrap items-center gap-2">
      <Input name="name" placeholder="New category, e.g. Payments" className="max-w-xs" required />
      <Button type="submit" variant="outline" disabled={pending}>
        Add
      </Button>
      <Feedback state={state} />
    </form>
  );
}
