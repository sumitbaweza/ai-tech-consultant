import { notFound } from "next/navigation";
import { processEmailAction } from "@/app/actions";
import { ActionButton } from "@/components/action-button";
import { DraftPanel } from "@/components/draft-panel";
import { Badge } from "@/components/ui/badge";
import { getDb } from "@/db/client";
import { INTENT_LABELS, type Intent } from "@/lib/domain";
import { isMailList } from "@/lib/folders";
import { getEmail } from "@/lib/mailbox";
import { getViewer } from "@/lib/viewer";

/** The reading pane. React escapes the text it renders, so an email body can't inject markup. */
export default async function EmailPage({ params }: { params: Promise<{ folder: string; id: string }> }) {
  const { folder: list, id } = await params;
  if (!isMailList(list) || !/^\d{1,9}$/.test(id)) notFound();
  const { mailbox } = await getViewer();
  if (!mailbox) notFound();
  const found = await getEmail(getDb(), mailbox.id, list, Number(id));
  if (!found) notFound();
  const { email, analysis, draft } = found;

  const sentAt = email.receivedAt.toLocaleString("en-US", { timeZone: "UTC", dateStyle: "medium", timeStyle: "short" });
  const flags = [...(analysis?.riskFlags ?? []), ...(analysis?.guardFlags ?? [])];

  return (
    <article className="p-6">
      <h2 className="text-xl">{email.subject || "(no subject)"}</h2>
      <div className="mt-4 flex flex-wrap items-start justify-between gap-x-4 gap-y-1 text-sm">
        <div className="min-w-0 break-words">
          <span className="font-medium">{email.fromName}</span>{" "}
          <span className="text-muted-foreground">&lt;{email.fromAddress}&gt;</span>
          <div className="text-muted-foreground">to {email.toAddresses.join(", ")}</div>
        </div>
        <time dateTime={email.receivedAt.toISOString()} className="text-muted-foreground shrink-0">
          {sentAt} UTC
        </time>
      </div>
      <div className="mt-6 text-sm leading-relaxed whitespace-pre-wrap">{email.bodyText || "(empty email)"}</div>

      {email.folder === "inbox" && analysis === undefined && (
        <div className="bg-muted mt-8 flex flex-col gap-3 rounded-lg p-4 text-sm">
          <p>Not classified or drafted yet.</p>
          <ActionButton
            action={processEmailAction}
            label="Classify and draft"
            pendingLabel="Classifying and drafting..."
            fields={{ emailId: String(email.id) }}
          />
        </div>
      )}

      {analysis !== undefined && (
        <section className="mt-8 flex flex-col gap-2 text-sm">
          {analysis.status === "done" ? (
            <>
              <div className="flex flex-wrap gap-1.5">
                <Badge>{INTENT_LABELS[analysis.intent as Intent]}</Badge>
                <Badge variant="outline">{analysis.productArea}</Badge>
                <Badge variant="outline">Sentiment: {analysis.sentiment}</Badge>
                <Badge variant="outline">Urgency: {analysis.urgency}</Badge>
                <Badge variant="outline">Language: {analysis.language}</Badge>
                {flags.map((flag) => (
                  <Badge key={flag} variant="outline" className="border-red-300 text-red-700">
                    {flag.replaceAll("_", " ")}
                  </Badge>
                ))}
              </div>
              {analysis.summary !== null && <p className="text-muted-foreground">{analysis.summary}</p>}
            </>
          ) : (
            <p className="text-destructive">Classification failed: {analysis.error}</p>
          )}
          {analysis.status === "done" && analysis.error !== null && (
            <p className="text-destructive">{analysis.error}</p>
          )}
          {analysis.intent === "spam" && <p className="text-muted-foreground">Spam: no reply drafted.</p>}
          {(analysis.status === "failed" || (analysis.error !== null && draft === undefined)) && (
            <ActionButton
              action={processEmailAction}
              label="Try again"
              pendingLabel="Working..."
              fields={{ emailId: String(email.id) }}
              variant="outline"
            />
          )}
        </section>
      )}

      {draft !== undefined && (
        <DraftPanel
          draft={{
            id: draft.id,
            emailId: draft.emailId,
            categoryKey: draft.categoryKey,
            status: draft.status,
            subject: draft.subject,
            body: draft.body,
            finalSubject: draft.finalSubject,
            finalBody: draft.finalBody,
            edited: draft.edited,
            editRatio: draft.editRatio,
            sentBy: draft.sentBy,
            blockers: draft.blockers,
            openQuestions: draft.openQuestions,
            confidence: draft.confidence,
            brainStatus: draft.brainStatus,
            facts: draft.facts,
            citedSourceIds: draft.citedSourceIds,
            scheduledFor: draft.scheduledFor?.toISOString() ?? null,
            sampledForReview: draft.sampledForReview,
            reviewResult: draft.reviewResult,
            sendError: draft.sendError,
            playbookVersion: draft.playbookVersion,
          }}
        />
      )}
    </article>
  );
}
