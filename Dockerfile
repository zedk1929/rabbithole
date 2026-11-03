FROM python:3.13-slim

WORKDIR /app

# Ensure SSL certificates and curl
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv binary
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy project manifest, lockfile, README, data, and source code
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
COPY data/ ./data/
COPY run.py ./

# Install python dependencies and application
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"
ENV HOST=0.0.0.0
ENV PORT=8000
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["sh", "-c", "uvicorn rabbithole_app.server:app --host 0.0.0.0 --port ${PORT:-8000}"]
