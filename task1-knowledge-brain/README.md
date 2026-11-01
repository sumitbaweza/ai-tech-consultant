# Task 1: Knowledge Brain (Python)

Crawls the Brilliant Directories help center, describes its screenshots, keeps it fresh, and answers questions with citations through an API. A question it can't answer goes to a human expert, and the expert's answer becomes knowledge it cites from then on.

## Services

| Service | Runs | What it does |
| --- | --- | --- |
| `brain-api` | `app/api/main.py` (FastAPI) | `/v1` API, docs at http://localhost:8000/docs |
| `brain-worker` | `app/jobs/worker.py` | Polite crawl (2 tasks, 1 request a second), scheduled sitemap checks, screenshot descriptions, embeddings |
| `brain-ui` | `ui/main.py` (Streamlit) | Screens at http://localhost:8501, behind a sign-in |
| `brain-mcp` | `app/mcp_server.py` | MCP tools at http://localhost:8001/mcp |
| `brain-seed` | `app/seed.py` | On a first start, loads `seed/brain-seed.jsonl.gz`: 1,700 pages, 6,458 screenshot descriptions and 10,825 embedded chunks (about 46 seconds) |
| `brain-migrate` | Alembic | Brings the `brain` schema up to date |

## How an answer is made

1. A follow-up is rewritten into a question that stands on its own (Haiku).
2. Hybrid search: pgvector by meaning plus Postgres full-text by exact words, merged with Reciprocal Rank Fusion. Only active pages count.
3. Nothing close (cosine similarity under 0.30)? The answer is "I don't know" and Claude isn't called.
4. The router picks the model. Haiku answers when the best hit scores 0.60 or more and the question asks one thing; everything else goes to Sonnet. A Haiku answer that isn't confident falls back to Sonnet. The reply's `route` field says which model answered and why.
5. Citations that don't match a source are dropped; an answer left without one becomes "I don't know". Unanswered questions open an expert task.

## The API

| Endpoint | For |
| --- | --- |
| `POST /v1/ask` | An answer with citations, or an honest "I don't know" with `expert_task_id` |
| `POST /v1/search` | Ranked chunks with article links; no LLM, so fast and free |
| `POST /v1/capabilities/check` | Does a built-in setting already do what a client asks? `yes`, `partly`, `no` or `unknown`, with citations |
| `GET /v1/articles`, `/v1/articles/{id}`, `/v1/articles/{id}/history` | Pages by status (the archive), one article with its screenshots, its versions with diffs |
| `/v1/sitemaps`, `/v1/ingestions` | Add sitemaps, preview, confirm, check now, live progress (Server-Sent Events) |
| `GET /v1/stats` | Test 2's dashboard numbers and the latest run |
| `/v1/expert-tasks` | The expert queue: list, flag a wrong answer, answer, dismiss, archive an answer |

Every `/v1` call needs the header `X-API-Key` with a key from `BRAIN_API_KEYS`.

## Use it from Claude (MCP)

Four tools wrap the same API: `kb_ask`, `kb_search`, `kb_get_article` and `check_capability`.

- Claude Code: `claude mcp add --transport http knowledge-brain http://127.0.0.1:8001/mcp`
- Claude Desktop (stdio): add a server whose command is `uv` with the args `run --directory <repo>/task1-knowledge-brain python -m app.mcp_server`.

## Freshness

Each sitemap has a schedule (daily, weekly, every 15 or 30 days). The worker re-reads it, adds new URLs, and fetches every known page again. The content hash decides what changed, and only changed pages are described and embedded again. A URL missing from the sitemap is archived after 1, 2, 15 or 30 missed checks in a row, never deleted. "Check now" on the Sources screen runs a check at once.

## Develop

| What | Command |
| --- | --- |
| Tests (223) | `uv run --directory task1-knowledge-brain pytest` |
| Lint and strict types | `uv run --directory task1-knowledge-brain ruff check .`, then `pyright` the same way |
| Check the eval cases (free) | `uv run --directory task1-knowledge-brain python -m evals.run --check` |
| Run the eval | `... python -m evals.run`, with `--model haiku` or `--model sonnet` to compare against the router |
| Expert replay | `... python -m evals.run --expert-replay` |
| A new sign-in for the screens | `uv run --directory task1-knowledge-brain python -m ui.passwords <name>` |
| Refresh the seed from your database | `uv run --directory task1-knowledge-brain python -m app.seed export` |
