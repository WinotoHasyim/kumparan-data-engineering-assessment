# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.13.15
ARG UV_VERSION=0.12.3

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:${PYTHON_VERSION}-slim-bookworm AS base

COPY --from=uv /uv /uvx /usr/local/bin/

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

COPY pyproject.toml uv.lock ./

FROM base AS runtime-dependencies

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

FROM runtime-dependencies AS runtime

COPY demo_source/ ./demo_source/
COPY ingestion/ ./ingestion/

CMD ["python", "-m", "demo_source.mutate_articles", "list"]

FROM base AS test

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project

COPY demo_source/ ./demo_source/
COPY ingestion/ ./ingestion/
COPY tests/ ./tests/

CMD ["pytest", "-q"]
