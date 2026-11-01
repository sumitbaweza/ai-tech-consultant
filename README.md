# AI Tech Consultant

One system that does a tech consultant's slow work for an agency that builds sites on Brilliant Directories.

- **Task 1, Knowledge Brain (Python):** crawls the help center's 1,700 pages and 6,458 screenshots, keeps them fresh, and answers questions with citations. When it can't answer, it says so and asks a human expert, whose answer it then reuses.
- **Task 2, Inbox Agent (TypeScript):** classifies support email, drafts replies from the Brain's facts, learns from every edit, and earns auto-send for one kind of email at a time.
- **Task 3, Meeting to Scope (Python):** a team of agents turns client meetings into a scope, an hours estimate and a proposal. A human approves before anything reaches the client.

Tasks 2 and 3 use the Brain only through its HTTP API. Why it's built this way: [DESIGN.md](DESIGN.md). How well it works: [EVALS.md](EVALS.md). How AI was used to build it: [AI_LOG.md](AI_LOG.md).

## Run it

You need Docker Desktop (on Windows, with WSL2) and an Anthropic API key with credit.

1. `cp .env.example .env`, then set `ANTHROPIC_API_KEY` and `VOYAGE_API_KEY`.
2. `make up`. Without make: `docker compose up -d --build`.

The first start loads a finished crawl from `task1-knowledge-brain/seed/` in about a minute, so the Brain answers straight away.

| Open | What it is | Sign in with |
| --- | --- | --- |
| http://localhost:8501 | Brain screens: dashboard, sources, chat, expert queue, history | `admin` / `brain-demo` (example user in `.env.example`) |
| http://localhost:3000 | Inbox Agent, on 50 demo emails | `INBOX_DEMO_PASSWORD` (`inbox-demo`) |
| http://localhost:8502 | Meeting to Scope | Your name, in the sidebar |
| http://localhost:8000/docs | Brain API | Header `X-API-Key`: a key from `BRAIN_API_KEYS` |
| http://localhost:8002/docs | Scope API | Header `X-API-Key`: a key from `SCOPE_API_KEYS` |
| http://localhost:8001/mcp | The Brain as MCP tools | None (local only) |

The inbox uses the demo emails until you add a Google OAuth client and set `INBOX_MODE=gmail`. Nothing calls Claude at start-up: you start each draft, answer or meeting run yourself.

## Check it

| What | With make | Without make |
| --- | --- | --- |
| Task 1 tests (223) | `make test` | `uv run --directory task1-knowledge-brain pytest` |
| Task 2 tests (80) | `make inbox-test` | `pnpm --dir task2-inbox-agent test` |
| Task 3 tests (69) | `make scope-test` | `uv run --directory task3-scope-agents pytest` |
| Lint and strict types | `make lint`, `make inbox-lint`, `make scope-lint` | See the `Makefile` |
| Task 1 eval | `make eval` | `uv run --directory task1-knowledge-brain python -m evals.run` |

The tests use a fake Claude and a fake Brain, so they cost nothing. Database tests need Postgres running. The Python tasks need [uv](https://docs.astral.sh/uv/); the inbox needs Node 22 and pnpm (`npm install -g corepack@latest`, then `corepack enable pnpm`).

## Layout

| Folder | What | Details |
| --- | --- | --- |
| `task1-knowledge-brain/` | Crawl, search, answers, API, MCP server, screens | [README](task1-knowledge-brain/README.md) |
| `task2-inbox-agent/` | Inbox Agent: Next.js app and worker | [README](task2-inbox-agent/README.md) |
| `task3-scope-agents/` | Meeting to Scope: agents, API and screens | [README](task3-scope-agents/README.md) |
| `db/init/` | One Postgres role and schema per task | |
