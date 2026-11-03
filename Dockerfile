FROM python:3.13-slim

WORKDIR /app

# Ensure SSL certificates and curl
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv binary
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy project manifest and lockfile
COPY pyproject.toml uv.lock ./

# Install python dependencies without development extras
RUN uv sync --frozen --no-dev

# Copy application code
COPY src/ ./src/
COPY run.py ./

ENV PATH="/app/.venv/bin:$PATH"
ENV HOST=0.0.0.0
ENV PORT=8000
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["sh", "-c", "uvicorn rabbithole_app.server:app --host 0.0.0.0 --port ${PORT:-8000}"]
