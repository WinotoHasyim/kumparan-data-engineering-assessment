"""Continuously create deterministic source changes for live demonstrations."""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from typing import Any

from demo_source.cli_types import non_negative_int, positive_float, positive_int
from demo_source.db import connect
from demo_source.mutate_articles import (
    ArticleNotFoundError,
    hard_delete_article,
    insert_article,
    json_default,
    soft_delete_article,
    update_article,
)

DEFAULT_START_ID = 10_000_000


def next_event_number(database_url: str | None, start_id: int) -> int:
    """Resume after the highest workload-owned ID already in the source."""
    with connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT COALESCE(max(id) - %s + 1, 0) AS next_event
            FROM public.articles
            WHERE id >= %s
            """,
            (start_id, start_id),
        )
        event_number = cursor.fetchone()["next_event"]
    return event_number


def run_event(database_url: str | None, start_id: int, event_number: int) -> dict[str, Any]:
    """Insert one article and apply deterministic update/delete scenarios."""
    article_id = start_id + event_number
    occurred_at = datetime.now(UTC)
    operations: list[dict[str, Any]] = []

    inserted = insert_article(
        database_url,
        article_id=article_id,
        title=f"Live article {event_number:08d}",
        content=f"Deterministic live workload event {event_number}.",
        author_id=(event_number % 5_000) + 1,
        published_at=occurred_at,
        created_at=occurred_at,
        updated_at=occurred_at,
    )
    operations.append({"action": "insert", "article_id": inserted["id"]})

    planned_operations = (
        (5, 2, "update"),
        (11, 4, "soft-delete"),
        (17, 6, "hard-delete"),
    )
    for cadence, offset, action in planned_operations:
        if event_number < offset or (event_number + 1) % cadence != 0:
            continue
        target_id = article_id - offset
        try:
            if action == "update":
                update_article(
                    database_url,
                    target_id,
                    title=f"Live article {target_id} updated at event {event_number}",
                )
            elif action == "soft-delete":
                soft_delete_article(database_url, target_id, deleted_at=occurred_at)
            else:
                hard_delete_article(database_url, target_id)
            operations.append({"action": action, "article_id": target_id})
        except ArticleNotFoundError:
            operations.append({"action": f"{action}-skipped", "article_id": target_id})

    return {
        "event_number": event_number,
        "occurred_at": occurred_at,
        "operations": operations,
    }


def run_workload(
    *,
    database_url: str | None,
    start_id: int,
    interval_seconds: float,
    max_events: int,
) -> None:
    """Run until interrupted or until a non-zero max-events limit is reached."""
    event_number = next_event_number(database_url, start_id)
    produced = 0
    next_run = time.monotonic()

    while max_events == 0 or produced < max_events:
        result = run_event(database_url, start_id, event_number)
        print(json.dumps(result, default=json_default, sort_keys=True), flush=True)
        produced += 1
        event_number += 1

        if max_events != 0 and produced >= max_events:
            break
        next_run += interval_seconds
        time.sleep(max(0.0, next_run - time.monotonic()))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", help="Override SOURCE_ADMIN_DATABASE_URL")
    parser.add_argument("--start-id", type=positive_int, default=DEFAULT_START_ID)
    parser.add_argument("--interval-seconds", type=positive_float, default=15.0)
    parser.add_argument(
        "--max-events",
        type=non_negative_int,
        default=0,
        help="Stop after this many inserts; 0 runs until interrupted",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        run_workload(
            database_url=args.database_url,
            start_id=args.start_id,
            interval_seconds=args.interval_seconds,
            max_events=args.max_events,
        )
    except KeyboardInterrupt:
        print("Live workload stopped.", flush=True)


if __name__ == "__main__":
    main()
