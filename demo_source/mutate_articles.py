"""Apply controlled source mutations for incremental and delete demonstrations."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from typing import Any

from demo_source.cli_types import parse_utc_timestamp, positive_int
from demo_source.db import connect

Article = dict[str, Any]


class ArticleNotFoundError(RuntimeError):
    """Raised when a requested article does not exist."""


def require_article(action: str, article: Article | None) -> Article:
    """Return an affected article or raise a domain-level error."""
    if article is None:
        raise ArticleNotFoundError(f"No article was affected by action: {action}")
    return article


def json_default(value: Any) -> str:
    """Serialize timezone-aware datetimes as UTC ISO-8601 strings."""
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("refusing to serialize a timezone-naive datetime")
        return value.astimezone(UTC).isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def print_result(action: str, article: Article) -> None:
    """Print a committed mutation as machine-readable evidence."""
    print(json.dumps({"action": action, "article": article}, default=json_default, indent=2))


def insert_article(
    database_url: str | None,
    *,
    article_id: int | None,
    title: str,
    content: str,
    author_id: int,
    published_at: datetime | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> Article:
    """Insert one article and return it after the transaction commits."""
    with connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO public.articles (
                id, title, content, published_at, author_id, created_at, updated_at
            )
            VALUES (
                COALESCE(%s, nextval(pg_get_serial_sequence('public.articles', 'id'))),
                %s,
                %s,
                %s,
                %s,
                COALESCE(%s, clock_timestamp()),
                COALESCE(%s, clock_timestamp())
            )
            RETURNING *
            """,
            (article_id, title, content, published_at, author_id, created_at, updated_at),
        )
        article = require_article("insert", cursor.fetchone())
    return article


def update_article(
    database_url: str | None,
    article_id: int,
    *,
    title: str | None = None,
    content: str | None = None,
    published_at: datetime | None = None,
    author_id: int | None = None,
) -> Article:
    """Update explicitly supplied fields from a fixed column allowlist."""
    candidates = {
        "title": title,
        "content": content,
        "published_at": published_at,
        "author_id": author_id,
    }
    assignments = [f"{column} = %s" for column, value in candidates.items() if value is not None]
    values = [value for value in candidates.values() if value is not None]
    if not assignments:
        raise ValueError(
            "Provide at least one of --title, --content, --published-at, or --author-id"
        )

    values.append(article_id)
    with connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE public.articles SET {', '.join(assignments)} WHERE id = %s RETURNING *",
            values,
        )
        article = require_article("update", cursor.fetchone())
    return article


def soft_delete_article(
    database_url: str | None,
    article_id: int,
    *,
    deleted_at: datetime | None = None,
    preserve_updated_at: bool = False,
) -> Article:
    """Set deleted_at, optionally simulating a stale updated_at value."""
    with connect(database_url) as connection, connection.cursor() as cursor:
        if preserve_updated_at:
            cursor.execute("SET LOCAL app.preserve_updated_at = 'on'")
        cursor.execute(
            """
            UPDATE public.articles
            SET deleted_at = COALESCE(%s, clock_timestamp())
            WHERE id = %s
            RETURNING *
            """,
            (deleted_at, article_id),
        )
        article = require_article("soft-delete", cursor.fetchone())
    return article


def restore_article(database_url: str | None, article_id: int) -> Article:
    """Restore one soft-deleted article."""
    with connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            "UPDATE public.articles SET deleted_at = NULL WHERE id = %s RETURNING *",
            (article_id,),
        )
        article = require_article("restore", cursor.fetchone())
    return article


def hard_delete_article(database_url: str | None, article_id: int) -> Article:
    """Physically remove one article and return its former values."""
    with connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM public.articles WHERE id = %s RETURNING *",
            (article_id,),
        )
        article = require_article("hard-delete", cursor.fetchone())
    return article


def set_boundary_case(
    database_url: str | None,
    article_id: int,
    *,
    updated_at: datetime,
    title: str,
) -> Article:
    """Set an exact timestamp for interval-boundary extraction tests."""
    with connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE public.articles
            SET title = %s,
                updated_at = %s
            WHERE id = %s
            RETURNING *
            """,
            (title, updated_at, article_id),
        )
        article = require_article("set-boundary-case", cursor.fetchone())
    return article


def show_article(database_url: str | None, article_id: int) -> Article:
    """Return one article without changing it."""
    with connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT * FROM public.articles WHERE id = %s", (article_id,))
        article = require_article("show", cursor.fetchone())
    return article


def list_articles(database_url: str | None, limit: int) -> list[Article]:
    """Return article metadata in stable ID order."""
    with connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, title, author_id, published_at, created_at, updated_at, deleted_at
            FROM public.articles
            ORDER BY id
            LIMIT %s
            """,
            (limit,),
        )
        articles = cursor.fetchall()
    return articles


# CLI adapters keep argparse concerns out of the database operation functions.
def handle_insert(args: argparse.Namespace) -> None:
    article = insert_article(
        args.database_url,
        article_id=args.id,
        title=args.title,
        content=args.content,
        author_id=args.author_id,
        published_at=args.published_at,
        created_at=args.created_at,
        updated_at=args.updated_at,
    )
    print_result("insert", article)


def handle_update(args: argparse.Namespace) -> None:
    article = update_article(
        args.database_url,
        args.id,
        title=args.title,
        content=args.content,
        published_at=args.published_at,
        author_id=args.author_id,
    )
    print_result("update", article)


def handle_soft_delete(args: argparse.Namespace) -> None:
    article = soft_delete_article(
        args.database_url,
        args.id,
        deleted_at=args.at,
        preserve_updated_at=args.preserve_updated_at,
    )
    print_result("soft-delete", article)


def handle_restore(args: argparse.Namespace) -> None:
    print_result("restore", restore_article(args.database_url, args.id))


def handle_hard_delete(args: argparse.Namespace) -> None:
    print_result("hard-delete", hard_delete_article(args.database_url, args.id))


def handle_boundary(args: argparse.Namespace) -> None:
    article = set_boundary_case(
        args.database_url,
        args.id,
        updated_at=args.at,
        title=args.title,
    )
    print_result("set-boundary-case", article)


def handle_show(args: argparse.Namespace) -> None:
    print_result("show", show_article(args.database_url, args.id))


def handle_list(args: argparse.Namespace) -> None:
    articles = list_articles(args.database_url, args.limit)
    print(
        json.dumps(
            {"action": "list", "count": len(articles), "articles": articles},
            default=json_default,
            indent=2,
        )
    )


def add_common_id_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("id", type=positive_int, help="Article ID")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", help="Override SOURCE_ADMIN_DATABASE_URL")
    subparsers = parser.add_subparsers(dest="command", required=True)

    insert_parser = subparsers.add_parser("insert", help="Insert one article")
    insert_parser.add_argument("--id", type=positive_int)
    insert_parser.add_argument("--title", default="Artikel baru dari mutation CLI")
    insert_parser.add_argument("--content", default="Konten artikel baru untuk incremental load.")
    insert_parser.add_argument("--author-id", type=positive_int, default=999)
    insert_parser.add_argument("--published-at", type=parse_utc_timestamp)
    insert_parser.add_argument("--created-at", type=parse_utc_timestamp)
    insert_parser.add_argument("--updated-at", type=parse_utc_timestamp)
    insert_parser.set_defaults(handler=handle_insert)

    update_parser = subparsers.add_parser("update", help="Update an existing article")
    add_common_id_argument(update_parser)
    update_parser.add_argument("--title")
    update_parser.add_argument("--content")
    update_parser.add_argument("--published-at", type=parse_utc_timestamp)
    update_parser.add_argument("--author-id", type=positive_int)
    update_parser.set_defaults(handler=handle_update)

    soft_delete_parser = subparsers.add_parser("soft-delete", help="Set deleted_at")
    add_common_id_argument(soft_delete_parser)
    soft_delete_parser.add_argument("--at", type=parse_utc_timestamp)
    soft_delete_parser.add_argument(
        "--preserve-updated-at",
        action="store_true",
        help="Simulate a source where setting deleted_at does not update updated_at",
    )
    soft_delete_parser.set_defaults(handler=handle_soft_delete)

    restore_parser = subparsers.add_parser("restore", help="Clear deleted_at")
    add_common_id_argument(restore_parser)
    restore_parser.set_defaults(handler=handle_restore)

    hard_delete_parser = subparsers.add_parser("hard-delete", help="Physically remove a row")
    add_common_id_argument(hard_delete_parser)
    hard_delete_parser.set_defaults(handler=handle_hard_delete)

    boundary_parser = subparsers.add_parser(
        "set-boundary-case",
        help="Set an exact updated_at timestamp for interval-boundary tests",
    )
    add_common_id_argument(boundary_parser)
    boundary_parser.add_argument("--at", type=parse_utc_timestamp, required=True)
    boundary_parser.add_argument("--title", default="Exact interval boundary mutation")
    boundary_parser.set_defaults(handler=handle_boundary)

    show_parser = subparsers.add_parser("show", help="Show one article")
    add_common_id_argument(show_parser)
    show_parser.set_defaults(handler=handle_show)

    list_parser = subparsers.add_parser("list", help="List article metadata")
    list_parser.add_argument("--limit", type=positive_int, default=20)
    list_parser.set_defaults(handler=handle_list)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.handler(args)
    except (ArticleNotFoundError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
