FROM oven/bun:1.3.12 AS frontend-build
WORKDIR /app

COPY package.json bun.lock ./
COPY frontend/package.json ./frontend/
COPY packages/react-email/package.json ./packages/react-email/
RUN bun install --frozen-lockfile

COPY frontend ./frontend
ARG VITE_API_URL=
RUN bun run --filter frontend build


FROM python:3.14.10-slim AS production

COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    WEB_CONCURRENCY=4

WORKDIR /app
RUN groupadd --system app && useradd --system --gid app --home-dir /app app

COPY pyproject.toml uv.lock ./
COPY backend/pyproject.toml backend/alembic.ini ./backend/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-workspace --package app

COPY backend/app ./backend/app
COPY backend/scripts/prestart.sh ./backend/scripts/prestart.sh
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package app

COPY --from=frontend-build /app/backend/app/frontend ./backend/app/frontend
WORKDIR /app/backend
USER app
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--no-access-log"]


FROM production AS development

USER root
WORKDIR /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --package app
COPY backend/scripts ./backend/scripts
COPY backend/tests ./backend/tests
WORKDIR /app/backend
