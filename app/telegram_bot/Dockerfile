# syntax=docker/dockerfile:1.7-labs

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps (minimal) and cleaning layer
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# --- Dependencies with cache mount ---
FROM base AS deps

COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

# --- Runtime image ---
FROM base AS runtime

ENV PATH="/usr/local/bin:${PATH}"
ENV PYTHONPATH="/app/src:/app/src/telegram_bot/generated"

# Copy installed libs from deps layer
COPY --from=deps /usr/local /usr/local

# Copy application source
COPY src ./src
COPY api ./api

# Non-root user (optional; comment out if you rely on root)
# RUN useradd -m appuser && chown -R appuser:appuser /app
# USER appuser

CMD ["python", "-m", "telegram_bot.main"]
