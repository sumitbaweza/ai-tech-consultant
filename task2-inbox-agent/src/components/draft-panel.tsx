"use client";

import { useActionState, useState } from "react";
import {
  type ActionState,
  discardDraftAction,
  processEmailAction,
  reviewSampleAction,
  sendDraftAction,
  undoScheduledAction,
} from "@/app/actions";
import { ActionButton } from "@/components/action-button";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { categoryLabel, type FactCheck } from "@/lib/domain";
import { isUnchanged } from "@/lib/text";

export type DraftView = {
  id: number;
  emailId: number;
  categoryKey: string;
  status: string;
  subject: string;
  body: string;
  finalSubject: string | null;
  finalBody: string | null;
  edited: boolean | null;
  editRatio: number | null;
  sentBy: string | null;
  blockers: string[];
  openQuestions: string[];
  confidence: number | null;
  brainStatus: string;
  facts: FactCheck[];
  citedSourceIds: string[];
  scheduledFor: string | null;
  sampledForReview: boolean;
  reviewResult: string | null;
  sendError: string | null;
  playbookVersion: number;
};

const FACT_STATUS: Record<FactCheck["status"], { label: string; className: string }> = {
  answered: { label: "Answered", className: "bg-green-100 text-green-900" },
  dont_know: { label: "Help center doesn't know", className: "bg-amber-100 text-amber-900" },
  unavailable: { label: "Knowledge base unavailable", className: "bg-red-100 text-red-900" },
};

function Facts({ facts }: { facts: FactCheck[] }) {
  if (facts.length === 0) return <p className="text-muted-foreground text-sm">No help-center questions in this email.</p>;
  return (
    <ul className="flex flex-col gap-3">
      {facts.map((fact) => (
        <li key={fact.question} className="text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{fact.question}</span>
            <span className={`rounded-md px-1.5 py-0.5 text-[11px] ${FACT_STATUS[fact.status].className}`}>
              {FACT_STATUS[fact.status].label}
            </span>
          </div>
          {fact.answer !== null && (
            <details className="text-muted-foreground mt-1">
              <summary className="cursor-pointer">Brain&apos;s answer</summary>
              <p className="mt-1 whitespace-pre-wrap">{fact.answer}</p>
            </details>
          )}
          {fact.citations.length > 0 && (
            <ul className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
              {fact.citations.map((citation) => (
                <li key={`${fact.question}-${citation.sourceId}`}>
                  <a href={citation.url} target="_blank" rel="noreferrer" className="text-primary text-xs underline">
                    {citation.origin === "expert" ? "Expert answer" : (citation.title ?? "Help-center article")}
                  </a>
                </li>
              ))}
            </ul>
          )}
        </li>
      ))}
    </ul>
  );
}

function Editor({ draft }: { draft: DraftView }) {
  const [subject, setSubject] = useState(draft.subject);
  const [body, setBody] = useState(draft.body);
  const [state, send, pending] = useActionState<ActionState, FormData>(sendDraftAction, {});
  const unchanged = isUnchanged(draft.body, body) && subject.trim() === draft.subject.trim();

  return (
    <form action={send} className="flex flex-col gap-3">
      <input type="hidden" name="draftId" value={draft.id} />
      <Input name="subject" value={subject} onChange={(event) => { setSubject(event.target.value); }} aria-label="Subject" />
      <textarea
        name="body"
        value={body}
        onChange={(event) => { setBody(event.target.value); }}
        aria-label="Reply"
        rows={14}
        className="border-input focus-visible:ring-ring/50 w-full rounded-lg border bg-transparent px-3 py-2 text-sm leading-relaxed outline-none focus-visible:ring-3"
      />
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" disabled={pending}>
          {pending ? "Sending..." : unchanged ? "Send as is" : "Send edited"}
        </Button>
        <span className="text-muted-foreground text-xs">
          {unchanged
            ? "Unedited sends count toward this category's auto-send."
            : "Editing restarts this category's count, and the edit becomes new rules."}
        </span>
      </div>
      {state.error !== undefined && <p className="text-destructive text-sm">{state.error}</p>}
      {state.message !== undefined && <p className="text-muted-foreground text-sm">{state.message}</p>}
    </form>
  );
}

function SentReply({ draft }: { draft: DraftView }) {
  const how =
    draft.sentBy === "auto"
      ? "Auto-sent"
      : draft.edited === true
        ? `Sent with edits (${String(Math.round((draft.editRatio ?? 0) * 100))}% changed)`
        : "Sent as drafted";
  return (
    <div className="flex flex-col gap-2 text-sm">
      <p className="font-medium">{how}</p>
      <p className="font-medium">{draft.finalSubject}</p>
      <p className="whitespace-pre-wrap">{draft.finalBody}</p>
      {draft.edited === true && (
        <details className="text-muted-foreground">
          <summary className="cursor-pointer">The AI draft before the edit</summary>
          <p className="mt-1 whitespace-pre-wrap">{draft.body}</p>
        </details>
      )}
    </div>
  );
}

/** The draft under the email: what the Brain knew, why it can't auto-send, and the reply. */
export function DraftPanel({ draft }: { draft: DraftView }) {
  const open = draft.status === "ready" || draft.status === "failed";
  return (
    <Card className="mt-8">
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          AI draft
          <Badge variant="outline">{categoryLabel(draft.categoryKey)}</Badge>
          {draft.playbookVersion > 0 && <Badge variant="secondary">Playbook v{draft.playbookVersion}</Badge>}
          {draft.confidence !== null && (
            <Badge variant="secondary">Confidence {Math.round(draft.confidence * 100)}%</Badge>
          )}
        </CardTitle>
        <CardDescription>
          Facts come from the Knowledge Brain&apos;s help-center answers. The email itself is treated as data, never as
          instructions.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-6">
        <section>
          <h3 className="mb-2 text-sm font-semibold">Facts</h3>
          <Facts facts={draft.facts} />
        </section>

        {draft.blockers.length > 0 && (
          <section>
            <h3 className="mb-2 text-sm font-semibold">Never auto-sends, because</h3>
            <ul className="list-disc pl-5 text-sm">
              {draft.blockers.map((blocker) => (
                <li key={blocker}>{blocker}</li>
              ))}
            </ul>
          </section>
        )}

        {draft.openQuestions.length > 0 && (
          <section>
            <h3 className="mb-2 text-sm font-semibold">Open questions for a teammate</h3>
            <ul className="list-disc pl-5 text-sm">
              {draft.openQuestions.map((question) => (
                <li key={question}>{question}</li>
              ))}
            </ul>
          </section>
        )}

        {draft.sendError !== null && draft.status === "failed" && (
          <p className="text-destructive text-sm">The last send failed: {draft.sendError}</p>
        )}

        {draft.status === "scheduled" && (
          <section className="flex flex-col gap-3 rounded-lg bg-amber-50 p-4 text-sm">
            <p>
              This category has earned auto-send. The reply goes out at{" "}
              <strong>{draft.scheduledFor === null ? "soon" : new Date(draft.scheduledFor).toLocaleTimeString()}</strong>{" "}
              unless you stop it.
            </p>
            <ActionButton
              action={undoScheduledAction}
              label="Undo: let me review it"
              pendingLabel="Stopping..."
              fields={{ draftId: String(draft.id) }}
              variant="outline"
            />
          </section>
        )}

        {open || draft.status === "scheduled" ? <Editor draft={draft} /> : null}
        {(draft.status === "sent" || draft.status === "auto_sent") && <SentReply draft={draft} />}
        {draft.status === "discarded" && <p className="text-muted-foreground text-sm">This draft was discarded.</p>}

        {draft.status === "auto_sent" && draft.sampledForReview && draft.reviewResult === null && (
          <section className="flex flex-col gap-2 rounded-lg bg-blue-50 p-4 text-sm">
            <p>This auto-sent reply was picked for a spot check (1 in N). Was it good?</p>
            <div className="flex gap-2">
              <ActionButton
                action={reviewSampleAction}
                label="Good"
                pendingLabel="Saving..."
                fields={{ draftId: String(draft.id), verdict: "good" }}
              />
              <ActionButton
                action={reviewSampleAction}
                label="Bad: stop auto-send"
                pendingLabel="Saving..."
                fields={{ draftId: String(draft.id), verdict: "bad" }}
                variant="destructive"
              />
            </div>
          </section>
        )}
        {draft.reviewResult !== null && (
          <p className="text-muted-foreground text-sm">Spot check: {draft.reviewResult}</p>
        )}

        {(open || draft.status === "discarded") && (
          <div className="flex flex-wrap gap-2 border-t pt-4">
            {open && (
              <ActionButton
                action={discardDraftAction}
                label="Discard"
                pendingLabel="Discarding..."
                fields={{ draftId: String(draft.id) }}
                variant="ghost"
              />
            )}
            <ActionButton
              action={processEmailAction}
              label="Draft again"
              pendingLabel="Drafting..."
              fields={{ emailId: String(draft.emailId) }}
              variant="ghost"
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
