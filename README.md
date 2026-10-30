# AI Tech Consultant

One system that does a tech consultant's slow work. It knows the product (Task 1, Knowledge Brain), answers the inbox (Task 2, Inbox Agent), and turns client meetings into a scope of work (Task 3, Meeting to Scope). Tasks 2 and 3 reach Task 1 only through its API.

> Work in progress: Day 1 of 4. This page gets its final form on Day 4.

## Run it

You need Docker Desktop (on Windows, with WSL2).

1. `cp .env.example .env`, then add your `ANTHROPIC_API_KEY` and `VOYAGE_API_KEY`.
2. `make up`
3. Open the Brain's API docs at http://localhost:8000/docs. Use a key from `BRAIN_API_KEYS` in `.env`.

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

Tests, lint and evals run on your machine and need [uv](https://docs.astral.sh/uv/). `make eval-check` checks the eval cases against the crawled pages for free; `make eval` runs them through the real answer path, needs both API keys, and saves the results in `task1-knowledge-brain/evals/results/`.

## Layout

| Folder | What it holds | Language |
| --- | --- | --- |
| `task1-knowledge-brain/` | Knowledge Brain: crawl, search, answer, API | Python |
| `task2-inbox-agent/` | Inbox Agent | TypeScript |
| `task3-scope-agents/` | Meeting to Scope agent team | Python |
| `db/init/` | Creates one Postgres role and schema per task | Shell and SQL |
| `docs/` | Brief summary, blueprint, planning notes, diagrams | |
