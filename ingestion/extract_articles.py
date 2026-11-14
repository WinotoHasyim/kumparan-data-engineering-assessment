"""Ingest one fixed article interval into Parquet files and a manifest."""

from __future__ import annotations

import argparse
import json
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
from google.cloud import storage
from google.cloud.storage.bucket import Bucket
from psycopg.rows import dict_row

from demo_source.cli_types import parse_utc_timestamp, positive_int

READER_DATABASE_URL_ENV = "SOURCE_READER_DATABASE_URL"
GCS_RAW_BUCKET_ENV = "GCS_RAW_BUCKET"
GCP_PROJECT_ID_ENV = "GCP_PROJECT_ID"
DEFAULT_GCS_PREFIX = "raw"
GCS_UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024
DEFAULT_ROWS_PER_FILE = 50_000
DEFAULT_FETCH_SIZE = DEFAULT_ROWS_PER_FILE
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


def resolve_gcs_bucket_name(explicit_name: str | None = None) -> str:
    """Return the raw bucket name or fail when it is not configured."""
    bucket_name = explicit_name or os.environ.get(GCS_RAW_BUCKET_ENV)
    if not bucket_name:
        raise RuntimeError(
            f"{GCS_RAW_BUCKET_ENV} is required; set it in the runtime environment "
            "or pass --gcs-bucket"
        )
    return bucket_name


def build_attempt_prefix(
    gcs_prefix: str,
    *,
    extracted_at: datetime,
    batch_id: str,
    attempt_id: str,
) -> str:
    """Build the immutable GCS object prefix for one extraction attempt."""
    ingest_date = extracted_at.astimezone(UTC).date().isoformat()
    segments = (
        gcs_prefix.strip("/"),
        f"source={SOURCE_NAME}",
        f"entity={ENTITY_NAME}",
        f"ingest_date={ingest_date}",
        f"window={batch_id}",
        f"attempt={attempt_id}",
    )
    return "/".join(segment for segment in segments if segment)


def fetch_page(
    cursor: psycopg.Cursor,
    *,
    window_start: datetime,
    window_end: datetime,
    last_updated_at: datetime | None,
    last_id: int | None,
    page_size: int,
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
            (window_start, window_end, page_size),
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
            (window_start, window_end, last_updated_at, last_id, page_size),
        )
    return cursor.fetchall()


def extract_articles(
    *,
    database_url: str | None,
    window_start: datetime,
    window_end: datetime,
    bucket: Bucket,
    gcs_prefix: str,
    fetch_size: int,
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
    if fetch_size < 1:
        raise ValueError("fetch-size must be at least 1")
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
    attempt_prefix = build_attempt_prefix(
        gcs_prefix,
        extracted_at=extracted_at,
        batch_id=batch_id,
        attempt_id=attempt_id,
    )

    parts: list[dict[str, Any]] = []
    total_rows = 0
    last_updated_at: datetime | None = None
    last_id: int | None = None
    source_exhausted = False

    with psycopg.connect(
        resolve_reader_database_url(database_url),
        row_factory=dict_row,
        connect_timeout=10,
        application_name="kumparan-article-extractor",
    ) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        with connection.cursor() as cursor:
            while not source_exhausted:
                rows = fetch_page(
                    cursor,
                    window_start=window_start,
                    window_end=window_end,
                    last_updated_at=last_updated_at,
                    last_id=last_id,
                    page_size=min(fetch_size, rows_per_file),
                )
                if not rows:
                    break

                part_name = f"part-{len(parts):05d}.parquet"
                object_name = f"{attempt_prefix}/{part_name}"
                blob = bucket.blob(object_name, chunk_size=GCS_UPLOAD_CHUNK_SIZE)
                blob.content_type = "application/vnd.apache.parquet"
                part_row_count = 0
                with (
                    blob.open(
                        "wb",
                        ignore_flush=True,
                        if_generation_match=0,
                    ) as output,
                    pq.ParquetWriter(
                        output,
                        ARTICLE_SCHEMA,
                        compression="snappy",
                    ) as writer,
                ):
                    while rows:
                        enriched_rows = [
                            {
                                **row,
                                "_attempt_id": attempt_id,
                                "_extracted_at": extracted_at,
                            }
                            for row in rows
                        ]
                        table = pa.Table.from_pylist(enriched_rows, schema=ARTICLE_SCHEMA)
                        writer.write_table(table)

                        row_count = len(rows)
                        part_row_count += row_count
                        total_rows += row_count
                        last_updated_at = rows[-1]["updated_at"]
                        last_id = rows[-1]["id"]

                        remaining_rows = rows_per_file - part_row_count
                        if remaining_rows == 0:
                            break

                        rows = fetch_page(
                            cursor,
                            window_start=window_start,
                            window_end=window_end,
                            last_updated_at=last_updated_at,
                            last_id=last_id,
                            page_size=min(fetch_size, remaining_rows),
                        )
                        if not rows:
                            source_exhausted = True
                blob.reload()
                parts.append(
                    {
                        "object": object_name,
                        "uri": f"gs://{bucket.name}/{object_name}",
                        "row_count": part_row_count,
                        "size_bytes": blob.size,
                        "crc32c": blob.crc32c,
                        "generation": int(blob.generation),
                    }
                )

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
    manifest_object = f"{attempt_prefix}/_manifest.json"
    manifest_blob = bucket.blob(manifest_object)
    manifest_blob.upload_from_string(
        f"{json.dumps(manifest, indent=2, sort_keys=True)}\n",
        content_type="application/json",
        if_generation_match=0,
    )

    return {
        "batch_id": batch_id,
        "attempt_id": attempt_id,
        "row_count": total_rows,
        "part_count": len(parts),
        "manifest_uri": f"gs://{bucket.name}/{manifest_object}",
    }


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line interface."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", help=f"Override {READER_DATABASE_URL_ENV}")
    parser.add_argument("--window-start", type=parse_utc_timestamp, required=True)
    parser.add_argument("--window-end", type=parse_utc_timestamp, required=True)
    parser.add_argument("--gcs-bucket", help=f"Override {GCS_RAW_BUCKET_ENV}")
    parser.add_argument("--gcs-prefix", default=DEFAULT_GCS_PREFIX)
    parser.add_argument("--gcp-project-id", help=f"Override {GCP_PROJECT_ID_ENV}")
    parser.add_argument(
        "--fetch-size",
        type=positive_int,
        default=DEFAULT_FETCH_SIZE,
        help="Maximum source rows held in one extraction page",
    )
    parser.add_argument(
        "--rows-per-file",
        type=positive_int,
        default=DEFAULT_ROWS_PER_FILE,
        help="Maximum rows written to one Parquet object",
    )
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
    client = storage.Client(project=args.gcp_project_id or os.environ.get(GCP_PROJECT_ID_ENV))
    bucket = client.bucket(resolve_gcs_bucket_name(args.gcs_bucket))
    result = extract_articles(
        database_url=args.database_url,
        window_start=args.window_start,
        window_end=args.window_end,
        bucket=bucket,
        gcs_prefix=args.gcs_prefix,
        fetch_size=args.fetch_size,
        rows_per_file=args.rows_per_file,
        execution_type=args.execution_type,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
