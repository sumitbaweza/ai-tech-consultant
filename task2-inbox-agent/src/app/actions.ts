"use server";

import { revalidatePath } from "next/cache";
import { signOut } from "@/auth";
import { getDb } from "@/db/client";
import { runMailboxSync } from "@/lib/gmail/sync";
import { getViewer } from "@/lib/viewer";

export async function signOutAction(): Promise<void> {
  await signOut({ redirectTo: "/signin" });
}

/** The Sync now button: sync the signed-in person's Gmail at once instead of waiting for the
 *  worker's next round. getViewer() checks the session, as every server action must. */
export async function syncNowAction(): Promise<void> {
  const { mailbox } = await getViewer();
  if (mailbox?.provider !== "google") return;
  await runMailboxSync(getDb(), mailbox.id);
  // Show the new emails: render the mail pages again
  revalidatePath("/", "layout");
}
