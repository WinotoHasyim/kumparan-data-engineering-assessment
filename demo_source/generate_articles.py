"""Generate deterministic synthetic articles for volume and backfill tests."""

from __future__ import annotations

import argparse
import json
import random
from datetime import UTC, datetime, timedelta
from typing import Any

from demo_source.cli_types import parse_utc_timestamp, positive_int
from demo_source.db import connect

HISTORICAL_START = datetime(2016, 1, 1, tzinfo=UTC)
HISTORICAL_CUTOFF = datetime(2026, 11, 13, tzinfo=UTC)

TOPICS = (
    "Ekonomi",
    "Teknologi",
    "Olahraga",
    "Gaya Hidup",
    "Politik",
    "Bisnis",
    "Sains",
    "Kuliner",
)


def build_article(
    article_id: int,
    rng: random.Random,
    start: datetime,
    end: datetime,
) -> tuple[Any, ...]:
    """Build one deterministic row from a seeded random generator."""
    total_seconds = int((end - start).total_seconds())
    created_at = start + timedelta(seconds=rng.randrange(total_seconds))
    author_id = rng.randint(1, 5_000)
    topic = rng.choice(TOPICS)
    title = f"{topic}: artikel sintetis {article_id}"

    word_count = rng.randint(80, 600)
    content = " ".join(f"kata{rng.randint(1, 250)}" for _ in range(word_count))

    # Keep every generated lifecycle timestamp inside the requested half-open
    # interval, even when created_at falls immediately before the upper bound.
    remaining_seconds = max(1, int((end - created_at).total_seconds()))

    is_draft = rng.random() < 0.08
    published_at = None
    if not is_draft:
        published_at = created_at + timedelta(seconds=rng.randrange(remaining_seconds))

    updated_at = created_at + timedelta(seconds=rng.randrange(remaining_seconds))
    if published_at is not None:
        updated_at = max(updated_at, published_at)

    deleted_at = None
    if rng.random() < 0.02:
        remaining_delete_seconds = max(1, int((end - updated_at).total_seconds()))
        deleted_at = updated_at + timedelta(seconds=rng.randrange(remaining_delete_seconds))
        updated_at = deleted_at

    return (
        article_id,
        title,
        content,
        published_at,
        author_id,
        created_at,
        updated_at,
        deleted_at,
    )


def generate_articles(
    *,
    database_url: str | None,
    count: int,
    seed: int,
    start_id: int,
    start: datetime,
    end: datetime,
    batch_size: int,
) -> dict[str, Any]:
    """Insert deterministic rows and return a machine-readable summary."""
    if count < 1:
        raise ValueError("count must be at least 1")
    if start_id < 1:
        raise ValueError("start-id must be at least 1")
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("start must include timezone information")
    if end.tzinfo is None or end.utcoffset() is None:
        raise ValueError("end must include timezone information")
    if start >= end:
        raise ValueError("start must be before end")
    if (end - start).total_seconds() < 1:
        raise ValueError("start and end must be at least one second apart")
    if batch_size < 1:
        raise ValueError("batch-size must be at least 1")

    rng = random.Random(seed)

    insert_sql = """
        INSERT INTO public.articles (
            id, title, content, published_at, author_id,
            created_at, updated_at, deleted_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
    """

    with connect(database_url) as connection, connection.cursor() as cursor:
        for batch_start in range(0, count, batch_size):
            batch_end = min(batch_start + batch_size, count)
            rows = [
                build_article(start_id + offset, rng, start, end)
                for offset in range(batch_start, batch_end)
            ]
            cursor.executemany(insert_sql, rows)

        cursor.execute(
            """
            SELECT count(*) AS row_count
            FROM public.articles
            WHERE id >= %s AND id < %s
            """,
            (start_id, start_id + count),
        )
        range_count = cursor.fetchone()["row_count"]

        cursor.execute(
            """
            SELECT setval(
                pg_get_serial_sequence('public.articles', 'id'),
                (SELECT max(id) FROM public.articles),
                true
            )
            """
        )

    return {
        "requested_count": count,
        "rows_present_in_id_range": range_count,
        "seed": seed,
        "start_id": start_id,
        "end_id_exclusive": start_id + count,
        "start_timestamp": start.isoformat(),
        "end_timestamp": end.isoformat(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", help="Override SOURCE_ADMIN_DATABASE_URL")
    parser.add_argument("--count", type=positive_int, default=100_000)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--start-id", type=positive_int, default=2_000_000)
    parser.add_argument("--start", type=parse_utc_timestamp, default=HISTORICAL_START)
    parser.add_argument("--end", type=parse_utc_timestamp, default=HISTORICAL_CUTOFF)
    parser.add_argument("--batch-size", type=positive_int, default=5_000)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = generate_articles(
        database_url=args.database_url,
        count=args.count,
        seed=args.seed,
        start_id=args.start_id,
        start=args.start,
        end=args.end,
        batch_size=args.batch_size,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
