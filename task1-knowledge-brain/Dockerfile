# The Brain's image, shared by brain-migrate and brain-api (and the worker from Step 5).
FROM python:3.12-slim

# uv, copied from its official image and pinned to the version that wrote uv.lock
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /bin/uv

WORKDIR /app

# Compile .pyc files at build time, copy packages into the venv instead of linking them,
# and use this image's Python rather than downloading another one
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never

# Dependencies first: Docker reuses this layer until pyproject.toml or uv.lock change
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev

COPY . .

# Run as an unprivileged user, not root
RUN useradd --create-home brain
USER brain

ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
