"""Shared database helpers for source-simulator commands."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row

ADMIN_DATABASE_URL_ENV = "SOURCE_ADMIN_DATABASE_URL"


def resolve_database_url(explicit_url: str | None = None) -> str:
    """Return explicit configuration or fail fast when it is missing."""
    database_url = explicit_url or os.environ.get(ADMIN_DATABASE_URL_ENV)
    if not database_url:
        raise RuntimeError(
            f"{ADMIN_DATABASE_URL_ENV} is required; set it in the runtime environment "
            "or pass --database-url"
        )
    return database_url


@contextmanager
def connect(database_url: str | None = None) -> Iterator[Connection]:
    """Open a transaction-scoped connection that returns rows as dictionaries."""
    with psycopg.connect(
        resolve_database_url(database_url),
        row_factory=dict_row,
        connect_timeout=10,
        application_name="kumparan-source-simulator",
    ) as connection:
        yield connection
