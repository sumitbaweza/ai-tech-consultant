# Start the system with `make up`. Windows has no `make` built in:
# paste a target's command lines into PowerShell instead.
.PHONY: up down logs ps psql test lint format eval eval-check eval-experts

# Build and start every service in the background
up:
	docker compose up -d --build

# Stop every service; the database keeps its data in the pgdata volume
down:
	docker compose down

# Follow the logs of every service
logs:
	docker compose logs -f

# List the services and their health
ps:
	docker compose ps

# Open a SQL prompt as the admin user
psql:
	docker compose exec postgres psql -U postgres -d consultant

# Run the Brain's tests. Database tests are skipped unless Postgres is up (`make up`).
test:
	uv run --directory task1-knowledge-brain pytest

# Lint, check formatting and type-check the Brain in strict mode
lint:
	uv run --directory task1-knowledge-brain ruff check .
	uv run --directory task1-knowledge-brain ruff format --check .
	uv run --directory task1-knowledge-brain pyright

# Fix what ruff can fix, and format the Brain's code
format:
	uv run --directory task1-knowledge-brain ruff check --fix .
	uv run --directory task1-knowledge-brain ruff format .

# Check the Brain's eval cases against the crawled pages: no API keys, no cost
eval-check:
	uv run --directory task1-knowledge-brain python -m evals.run --check

# Run the Brain's eval cases through the real answer path. Needs both API keys in .env.
eval:
	uv run --directory task1-knowledge-brain python -m evals.run

# Ask each expert answer's question again: how much the Brain learned. Needs both API keys.
eval-experts:
	uv run --directory task1-knowledge-brain python -m evals.run --expert-replay
