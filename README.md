# AI Tech Consultant

One system that does a tech consultant's slow work. It knows the product (Task 1, Knowledge Brain), answers the inbox (Task 2, Inbox Agent), and turns client meetings into a scope of work (Task 3, Meeting to Scope). Tasks 2 and 3 reach Task 1 only through its API.

> Work in progress: Day 1 of 4. This page gets its final form on Day 4.

## Run it

You need Docker Desktop (on Windows, with WSL2).

1. `cp .env.example .env`, then add your `ANTHROPIC_API_KEY` and `VOYAGE_API_KEY`.
2. `make up`
3. Open the Brain's screens at http://localhost:8501 and sign in. The example user in `.env.example` is `admin` with the password `brain-demo`; make your own with `uv run --directory task1-knowledge-brain python -m ui.passwords <name>`.
4. The Brain's API docs are at http://localhost:8000/docs. Use a key from `BRAIN_API_KEYS` in `.env`.
5. Open the Inbox Agent at http://localhost:3000. Without Google settings it runs in fixtures mode, on the emails in `task2-inbox-agent/fixtures/emails.json`: sign in with `INBOX_DEMO_PASSWORD` from `.env` (`inbox-demo` in `.env.example`).
6. Open Meeting to Scope at http://localhost:8502: pick a sample transcript from `task3-scope-agents/transcripts/` (stand-ins until the recorded meetings), watch the agents, answer their questions, then edit and approve the scope. Its API docs are at http://localhost:8002/docs (key: `SCOPE_API_KEYS` in `.env`). It asks the Brain with `BRAIN_API_KEY`, so set that in `.env` too.

Windows has no `make` built in, so here is each target without it:

| Make target | The same thing without make |
| --- | --- |
| `make up` | `docker compose up -d --build` |
| `make down` | `docker compose down` |
| `make logs` | `docker compose logs -f` |
| `make ps` | `docker compose ps` |
| `make psql` | `docker compose exec postgres psql -U postgres -d consultant` |
| `make test` | `uv run --directory task1-knowledge-brain pytest` |
| `make lint` | `uv run --directory task1-knowledge-brain ruff check .`, then `ruff format --check .`, then `pyright` the same way |
| `make eval-check` | `uv run --directory task1-knowledge-brain python -m evals.run --check` |
| `make eval` | `uv run --directory task1-knowledge-brain python -m evals.run` |
| `make eval-experts` | `uv run --directory task1-knowledge-brain python -m evals.run --expert-replay` |
| `make inbox-install` | `pnpm --dir task2-inbox-agent install --frozen-lockfile` |
| `make inbox-dev` | `pnpm --dir task2-inbox-agent dev` |
| `make inbox-test` | `pnpm --dir task2-inbox-agent test` |
| `make inbox-lint` | `pnpm --dir task2-inbox-agent typecheck`, then `pnpm --dir task2-inbox-agent lint` |
| `make scope-test` | `uv run --directory task3-scope-agents pytest` |
| `make scope-lint` | `uv run --directory task3-scope-agents ruff check .`, then `ruff format --check .`, then `pyright` the same way |

Tests, lint and evals run on your machine and need [uv](https://docs.astral.sh/uv/). `make eval-check` checks the eval cases against the crawled pages for free; `make eval` runs them through the real answer path, needs both API keys, and saves the results in `task1-knowledge-brain/evals/results/`. The inbox's checks need Node 22 and pnpm (`npm install -g corepack@latest`, then `corepack enable pnpm`).

## Use the Brain from Claude (MCP)

`make up` also starts `brain-mcp`, which offers the Brain as three MCP tools: `kb_ask` (an answer with citations), `kb_search` (matching chunks, no LLM) and `kb_get_article` (one article in full). Each tool calls the Brain's API with the first key in `BRAIN_API_KEYS`, so the API's rules apply.

- **Claude Code**, over HTTP:

  ```bash
  claude mcp add --transport http knowledge-brain http://127.0.0.1:8001/mcp
  ```

- **Claude Desktop**, over stdio: add this to `claude_desktop_config.json`, with your own path to the repo. It needs uv, and the API running.

  ```json
  {
    "mcpServers": {
      "knowledge-brain": {
        "command": "uv",
        "args": ["run", "--directory", "C:/path/to/repo/task1-knowledge-brain", "python", "-m", "app.mcp_server"]
      }
    }
  }
  ```

## Layout

| Folder | What it holds | Language |
| --- | --- | --- |
| `task1-knowledge-brain/` | Knowledge Brain: crawl, search, answer, API | Python |
| `task2-inbox-agent/` | Inbox Agent | TypeScript |
| `task3-scope-agents/` | Meeting to Scope agent team | Python |
| `db/init/` | Creates one Postgres role and schema per task | Shell and SQL |
| `docs/` | Brief summary, blueprint, planning notes, diagrams | |
