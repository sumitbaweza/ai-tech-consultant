# Task 2: Inbox Agent (TypeScript)

A Gmail-style app that reads the support inbox, classifies each email, gets facts from the Knowledge Brain's API (never its code), drafts a reply in the company voice, and learns from every human edit until a kind of email earns auto-send.

## What happens to each email

1. **Guard (code, no model):** strip quoted history and signatures, then flag empty or huge emails, prompt-injection patterns, card numbers and passwords, and legal threats.
2. **Classify (Haiku, `prompts/classify/v1.md`):** intent, product area (the categories in Settings), sentiment, urgency, language, risk flags, and up to 3 questions for the help center.
3. **Facts (the Brain's `POST /v1/ask`):** one answer per question, with citations. A Brain "don't know" or a Brain that is down is recorded as such, never guessed.
4. **Draft (Sonnet, `prompts/draft/v1.md`):** from the voice guide, the category's playbook rules, the last 3 approved replies of the same kind, and the facts. Fact ids that don't exist are dropped.
5. **Decide:** the draft waits for a human, unless its category is on auto-send and nothing blocks it. Then it sends itself after the undo window (5 minutes by default).
6. **Human acts:** send as is, edit and send, discard, or stop an auto-send. The draft, the sent text and the edit size are stored side by side.
7. **Learn (Sonnet, `prompts/learn_rules/v1.md`):** the worker turns each edit into 0 to 3 rules, saved as the category's next playbook version.

## The two definitions

- **Same kind** = the same intent × product area, such as "How-to × Header". Test 1 groups by intent and test 2 by product area; the pair satisfies both.
- **Unchanged** = the sent text equals the draft after ignoring whitespace and the signature. Any other edit resets the category's count. The edit size (share of characters changed) is the learning metric.

## Auto-send, and when it never happens

| State | How a category gets there | What happens |
| --- | --- | --- |
| Manual review | At the start, after any edit or discard, or when revoked | Every draft waits for a human |
| Earning | 1 to X−1 unedited sends in a row | The count shows on the Learning screen |
| Auto-send | X unedited sends in a row (default 20, set in Settings) | Sent after the undo window; 1 in 10 auto-sends goes to a human for a spot check |

Auto-send is **revoked** (back to manual review, with the reason logged) when a spot check is marked bad, or when a customer replies unhappily to an auto-sent email.

Whatever the category earned, a draft **never auto-sends** when the email is a refund, billing, quote, new-project, complaint or spam email; when it carries a legal threat, an angry customer, personal data or a prompt injection (from Claude or the guard); when the Brain didn't know every answer or was down; when the draft leaves questions open; or when the drafter's confidence is below 0.7. The reading pane lists the reasons for each draft.

## Two modes

| `INBOX_MODE` | The screens show | Sign-in |
| --- | --- | --- |
| `fixtures` (default) | `fixtures/emails.json`: 50 demo emails | `INBOX_DEMO_PASSWORD`, or Google if set up |
| `gmail` | The Gmail account you sign in with | Google: `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` |

- **Fixtures** are drafted on demand, so starting the system costs nothing: open an email and click **Classify and draft**, or click **Draft next 5** above the inbox. Replies go to the Sent folder; nothing leaves the machine.
- **Gmail:** the worker syncs every minute and drafts new mail from the last 3 days, at most 5 a minute. Sending uses the Gmail API, in the customer's thread.

The 50 fixture emails were drafted with Claude's help and reviewed by hand. They cover every intent and test 2's nine product areas, plus the hard cases: two questions in one email, angry customers, refunds inside and outside policy, three prompt injections, Spanish, French and German emails, a long thread, a question the help center can't answer, personal data, a legal threat, an empty email, a very long email, spam and a sales pitch.

## Screens

- **Mail:** Inbox, Drafts, Sent, Spam and Trash where Gmail has them. Profile and settings are top right.
- **Agent views:** **Needs review** (drafts waiting for a human, auto-sends in their undo window, unchecked spot checks) and **Auto-sent**.
- **Reading pane:** the email; its intent, area, sentiment, urgency, language and risks; the Brain's facts with links to the help-center articles; why the draft can't auto-send; and the editor with Send as is or Send edited, Discard and Draft again.
- **Learning:** each category's state and count toward X; the active playbook with each rule, why it was learned, the draft it came from and a Remove button; every version with "Use this version" (rollback); the edit-size trend; and why autonomy changed.
- **Settings:** categories (add, or click to switch one off), X, the spot-check rate, the undo window and the voice guide.

## Run it

- With everything else: `make up` (or `docker compose up -d --build`) from the repo root, then http://localhost:3000.
- On this machine with hot reload: `docker compose up -d postgres inbox-migrate`, then `pnpm install` and `pnpm dev` here.
- Draft one email from the command line (a smoke test; about $0.05):

  ```bash
  docker compose exec inbox-worker pnpm draft-email <email id>
  ```

Every Claude call is logged in `inbox.llm_calls`, with tokens, cost and latency. A smoke run of 6 Gmail emails and 2 fixture emails cost $0.08 on the inbox's side: Haiku classifies at about $0.0004 an email in 1.7 seconds, and Sonnet drafts at about $0.01 an email in 4 seconds.

## Layout

| Path | What it holds |
| --- | --- |
| `src/app/` | Pages: `signin`, `(mail)/mail/[folder]/[id]`, `(mail)/learning`, `(mail)/settings`; `actions.ts` holds every button's server action |
| `src/lib/pipeline/` | `guard.ts`, `classify.ts`, `facts.ts`, `draft.ts`, and `process.ts`, which runs them in order |
| `src/lib/autonomy.ts`, `src/lib/review.ts` | The auto-send rules (pure functions), and sending, discarding, undoing and spot checks |
| `src/lib/learn.ts`, `src/lib/text.ts` | Playbook versions, rules from edits and rollback; "unchanged" and the edit size |
| `src/lib/llm/` | The one Claude wrapper: versioned prompts, Zod structured outputs, `stop_reason` checks, fallbacks, the `llm_calls` ledger |
| `src/lib/brain.ts` | The Brain's HTTP client, checked against its OpenAPI schema |
| `src/lib/gmail/`, `src/lib/send.ts` | Gmail sync, parsing and sending |
| `src/worker.ts` | The `inbox-worker` loop: sync, draft new Gmail mail, send due auto-sends, learn |
| `prompts/` | `classify`, `draft` and `learn_rules`, one file per version; a change means a new version |
| `drizzle/` | SQL migrations written by `pnpm db:generate`. Remove the `CREATE SCHEMA` line from each new one: `db/init` creates the schema |
| `tests/` | Vitest: pure logic, the clients with fakes, and the whole pipeline on `consultant_test` with a fake Claude and a fake Brain |

## Checks

`pnpm typecheck`, `pnpm lint` and `pnpm test` (or `make inbox-lint` and `make inbox-test` from the root). Database tests skip when Postgres is down.
