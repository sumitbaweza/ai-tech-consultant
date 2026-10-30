# Task 2: Inbox Agent (TypeScript)

A Gmail-style app that reads the support inbox, classifies each email, gets facts from the Knowledge Brain's API (never its code) and drafts replies that learn from human edits.

**This version:** sign-in, the inbox in Postgres, fixtures mode, Gmail sync and the Gmail-style layout (Inbox, Drafts, Sent, Spam, Trash on the left; settings and the profile top right). Classifying, drafting and learning come next.

## Two modes

| `INBOX_MODE` | Emails come from | Sign-in |
| --- | --- | --- |
| `fixtures` (default) | `fixtures/emails.json`, loaded by `inbox-migrate` | `INBOX_DEMO_PASSWORD`, or Google if set up |
| `gmail` | The Gmail account you sign in with | Google: `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` |

Fixtures mode lets evals, tests and reviewers without access to the demo Gmail account see the same inbox.

## Gmail sync

Signing in with Google saves a refresh token (scopes: Gmail read, compose, send). The `inbox-worker` service then syncs every Gmail mailbox once a minute, and the Sync now button in the top bar syncs at once:

- **First sync:** the last 30 days, at most 500 messages, spam and trash included.
- **After that:** only what changed, from Gmail's change log (`history.list` from the saved `historyId`). If the log no longer reaches back (about a week), a full sync runs again.
- **Folders** come from Gmail's labels (trash and spam win); mail in no folder is stored as `archived` and not listed. Read state comes from the `UNREAD` label.
- **Bodies** are stored as plain text: the `text/plain` part, or the HTML part converted to text. No email HTML is ever rendered.
- **A refused sign-in** (revoked, or a refresh token older than 7 days while the OAuth app is in Testing) stops the sync for that mailbox and shows a banner until you sign in with Google again.

## Run it

- With everything else: `make up` (or `docker compose up -d --build`) from the repo root, then http://localhost:3000.
- On this machine with hot reload: `docker compose up -d postgres inbox-migrate`, then `pnpm install` and `pnpm dev` here.

## Layout

| Path | What it holds |
| --- | --- |
| `src/app/` | Pages: `signin`, `(mail)/mail/[folder]/[id]`, `(mail)/settings`, and Auth.js's route |
| `src/auth.ts`, `src/proxy.ts` | Sign-in (Google or the demo password) and the check that every page needs it |
| `src/db/` | Drizzle schema for the `inbox` Postgres schema, the connection, and `setup.ts` (migrate, then load fixtures) |
| `src/lib/` | Fixtures, mailbox queries, folders, formatting |
| `src/lib/gmail/` | `parse.ts` (Gmail message to row), `client.ts` (the Gmail calls and token refresh), `sync.ts` (full and incremental sync) |
| `src/worker.ts` | The `inbox-worker` loop |
| `src/components/` | The shell's parts; `ui/` holds shadcn/ui components |
| `drizzle/` | SQL migrations written by `pnpm db:generate`; never edit one already applied |
| `fixtures/emails.json` | The demo inbox |
| `tests/` | Vitest; database tests use `consultant_test` and skip when Postgres is down |

## Checks

`pnpm typecheck`, `pnpm lint` and `pnpm test` (or `make inbox-lint` and `make inbox-test` from the root).
