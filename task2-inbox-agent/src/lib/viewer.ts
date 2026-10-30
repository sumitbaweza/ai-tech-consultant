import { redirect } from "next/navigation";
import { cache } from "react";
import { auth } from "@/auth";
import { getDb } from "@/db/client";
import { getEnv } from "@/env";
import { currentMailbox } from "@/lib/mailbox";

/** Who is looking and at which mailbox. React's cache() runs it once per request, though
 *  the shell, the folder list and the open email all ask. */
export const getViewer = cache(async () => {
  const session = await auth();
  if (!session?.user) redirect("/signin");
  const env = getEnv();
  const mailbox = await currentMailbox(getDb(), env, session.user.email);
  return { user: session.user, mode: env.INBOX_MODE, mailbox };
});
