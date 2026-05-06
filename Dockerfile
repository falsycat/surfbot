FROM python:3.13-slim

# Install Node.js for claude CLI
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Install claude CLI
RUN npm install -g @anthropic-ai/claude-code

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN groupadd -g 1000 surfbot && useradd -u 1000 -g 1000 -m surfbot

# Put the venv outside /app so a repo volume mount won't shadow it
ENV UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

# Pre-install dependencies only (project itself installed at runtime from mounted source)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project && chown -R 1000:1000 /opt/venv

USER 1000:1000

CMD ["uv", "run", "surfbot"]
