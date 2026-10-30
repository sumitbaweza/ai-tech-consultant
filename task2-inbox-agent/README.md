# Task 2: Inbox Agent (TypeScript)

A Gmail-style app that reads the support inbox, classifies each email, gets facts from the Knowledge Brain's API (never its code) and drafts replies that learn from human edits.

**This version:** the skeleton. Sign-in, the inbox in Postgres, fixtures mode and the Gmail-style layout (Inbox, Drafts, Sent, Spam, Trash on the left; settings and the profile top right). Classifying, drafting and learning come next.

## Two modes

| `INBOX_MODE` | Emails come from | Sign-in |
| --- | --- | --- |
| `fixtures` (default) | `fixtures/emails.json`, loaded by `inbox-migrate` | `INBOX_DEMO_PASSWORD`, or Google if set up |
| `gmail` | The Gmail account you sign in with | Google: `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` |

Fixtures mode lets evals, tests and reviewers without access to the demo Gmail account see the same inbox.

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
| `src/components/` | The shell's parts; `ui/` holds shadcn/ui components |
| `drizzle/` | SQL migrations written by `pnpm db:generate`; never edit one already applied |
| `fixtures/emails.json` | The demo inbox |
| `tests/` | Vitest; database tests use `consultant_test` and skip when Postgres is down |

## Checks

`pnpm typecheck`, `pnpm lint` and `pnpm test` (or `make inbox-lint` and `make inbox-test` from the root).
