"""Ingest one fixed article interval into Parquet files and a manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
from psycopg.rows import dict_row

from demo_source.cli_types import parse_utc_timestamp, positive_int

READER_DATABASE_URL_ENV = "SOURCE_READER_DATABASE_URL"
DEFAULT_OUTPUT_DIR = Path("/data/raw")
SOURCE_NAME = "postgres"
ENTITY_NAME = "articles"
EXECUTION_TYPES = (
    "BACKFILL",
    "SCHEDULED",
)

ARTICLE_SCHEMA = pa.schema(
    [
        pa.field("id", pa.int64(), nullable=False),
        pa.field("title", pa.string(), nullable=False),
        pa.field("content", pa.string(), nullable=False),
        pa.field("published_at", pa.timestamp("us", tz="UTC")),
        pa.field("author_id", pa.int64(), nullable=False),
        pa.field("created_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("updated_at", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("deleted_at", pa.timestamp("us", tz="UTC")),
        pa.field("_attempt_id", pa.string(), nullable=False),
        pa.field("_extracted_at", pa.timestamp("us", tz="UTC"), nullable=False),
    ]
)

ARTICLE_COLUMNS = """
    id, title, content, published_at, author_id,
    created_at, updated_at, deleted_at
"""


def resolve_reader_database_url(explicit_url: str | None = None) -> str:
    """Return the read-only source URL or fail when it is not configured."""
    database_url = explicit_url or os.environ.get(READER_DATABASE_URL_ENV)
    if not database_url:
        raise RuntimeError(
            f"{READER_DATABASE_URL_ENV} is required; set it in the runtime environment "
            "or pass --database-url"
        )
    return database_url


def utc_text(value: datetime) -> str:
    """Serialize one timezone-aware datetime in canonical UTC form."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include timezone information")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def build_batch_id(window_start: datetime, window_end: datetime) -> str:
    """Build a stable identifier for one extraction interval."""
    start = window_start.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    end = window_end.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{start}_{end}"


def build_attempt_dir(
    output_dir: Path,
    *,
    extracted_at: datetime,
    batch_id: str,
    attempt_id: str,
) -> Path:
    """Build the local equivalent of the immutable GCS object prefix."""
    ingest_date = extracted_at.astimezone(UTC).date().isoformat()
    return (
        output_dir
        / f"source={SOURCE_NAME}"
        / f"entity={ENTITY_NAME}"
        / f"ingest_date={ingest_date}"
        / f"window={batch_id}"
        / f"attempt={attempt_id}"
    )


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of one file."""
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_page(
    cursor: psycopg.Cursor,
    *,
    window_start: datetime,
    window_end: datetime,
    last_updated_at: datetime | None,
    last_id: int | None,
    rows_per_file: int,
) -> list[dict[str, Any]]:
    """Fetch one stable keyset page from the extraction interval."""
    if last_updated_at is None:
        cursor.execute(
            f"""
            SELECT {ARTICLE_COLUMNS}
            FROM public.articles
            WHERE updated_at >= %s
              AND updated_at < %s
            ORDER BY updated_at, id
            LIMIT %s
            """,
            (window_start, window_end, rows_per_file),
        )
    else:
        cursor.execute(
            f"""
            SELECT {ARTICLE_COLUMNS}
            FROM public.articles
            WHERE updated_at >= %s
              AND updated_at < %s
              AND (updated_at, id) > (%s, %s)
            ORDER BY updated_at, id
            LIMIT %s
            """,
            (window_start, window_end, last_updated_at, last_id, rows_per_file),
        )
    return cursor.fetchall()


def extract_articles(
    *,
    database_url: str | None,
    window_start: datetime,
    window_end: datetime,
    output_dir: Path,
    rows_per_file: int,
    execution_type: str,
) -> dict[str, Any]:
    """Extract one interval and publish its manifest after all parts are complete."""
    if window_start.tzinfo is None or window_start.utcoffset() is None:
        raise ValueError("window-start must include timezone information")
    if window_end.tzinfo is None or window_end.utcoffset() is None:
        raise ValueError("window-end must include timezone information")
    if window_start >= window_end:
        raise ValueError("window-start must be before window-end")
    if rows_per_file < 1:
        raise ValueError("rows-per-file must be at least 1")
    if execution_type not in EXECUTION_TYPES:
        allowed = ", ".join(EXECUTION_TYPES)
        raise ValueError(f"execution-type must be one of: {allowed}")

    window_start = window_start.astimezone(UTC)
    window_end = window_end.astimezone(UTC)
    batch_id = build_batch_id(window_start, window_end)
    attempt_id = uuid.uuid4().hex
    extracted_at = datetime.now(UTC)
    attempt_dir = build_attempt_dir(
        output_dir,
        extracted_at=extracted_at,
        batch_id=batch_id,
        attempt_id=attempt_id,
    )
    attempt_dir.mkdir(parents=True)

    parts: list[dict[str, Any]] = []
    total_rows = 0
    last_updated_at: datetime | None = None
    last_id: int | None = None

    with psycopg.connect(
        resolve_reader_database_url(database_url),
        row_factory=dict_row,
        connect_timeout=10,
        application_name="kumparan-article-extractor",
    ) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        with connection.cursor() as cursor:
            while True:
                rows = fetch_page(
                    cursor,
                    window_start=window_start,
                    window_end=window_end,
                    last_updated_at=last_updated_at,
                    last_id=last_id,
                    rows_per_file=rows_per_file,
                )
                if not rows:
                    break

                enriched_rows = [
                    {
                        **row,
                        "_attempt_id": attempt_id,
                        "_extracted_at": extracted_at,
                    }
                    for row in rows
                ]
                part_name = f"part-{len(parts):05d}.parquet"
                part_path = attempt_dir / part_name
                table = pa.Table.from_pylist(enriched_rows, schema=ARTICLE_SCHEMA)
                pq.write_table(table, part_path, compression="snappy")
                parts.append(
                    {
                        "path": part_name,
                        "row_count": len(rows),
                        "size_bytes": part_path.stat().st_size,
                        "sha256": sha256_file(part_path),
                    }
                )
                total_rows += len(rows)
                last_updated_at = rows[-1]["updated_at"]
                last_id = rows[-1]["id"]

    manifest = {
        "manifest_version": 1,
        "source": SOURCE_NAME,
        "entity": ENTITY_NAME,
        "execution_type": execution_type,
        "batch_id": batch_id,
        "attempt_id": attempt_id,
        "extracted_at": utc_text(extracted_at),
        "window_start": utc_text(window_start),
        "window_end": utc_text(window_end),
        "row_count": total_rows,
        "part_count": len(parts),
        "parts": parts,
    }
    with (attempt_dir / "_manifest.json").open("x", encoding="utf-8") as manifest_file:
        json.dump(manifest, manifest_file, indent=2, sort_keys=True)
        manifest_file.write("\n")

    return {**manifest, "attempt_path": str(attempt_dir)}


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line interface."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", help=f"Override {READER_DATABASE_URL_ENV}")
    parser.add_argument("--window-start", type=parse_utc_timestamp, required=True)
    parser.add_argument("--window-end", type=parse_utc_timestamp, required=True)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--rows-per-file", type=positive_int, default=50_000)
    parser.add_argument(
        "--execution-type",
        choices=EXECUTION_TYPES,
        required=True,
        help="Reason this extraction attempt was started",
    )
    return parser


def main() -> None:
    """Run one extraction from command-line arguments."""
    args = build_parser().parse_args()
    result = extract_articles(
        database_url=args.database_url,
        window_start=args.window_start,
        window_end=args.window_end,
        output_dir=args.output_dir,
        rows_per_file=args.rows_per_file,
        execution_type=args.execution_type,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
