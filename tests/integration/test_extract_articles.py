import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pyarrow.parquet as pq
import pytest

from ingestion.extract_articles import extract_articles

ADMIN_URL = os.environ.get("SOURCE_ADMIN_DATABASE_URL")
READER_URL = os.environ.get("SOURCE_READER_DATABASE_URL")
TEST_IDS = (9_200_001, 9_200_002, 9_200_003, 9_200_004)


def require_database_urls() -> tuple[str, str]:
    if not ADMIN_URL or not READER_URL:
        pytest.skip("database URLs are not configured for integration tests")
    return ADMIN_URL, READER_URL


def delete_test_rows(admin_url: str) -> None:
    with psycopg.connect(admin_url) as connection, connection.cursor() as cursor:
        cursor.execute("DELETE FROM public.articles WHERE id = ANY(%s)", (list(TEST_IDS),))


@pytest.mark.integration
def test_extract_uses_half_open_window_and_keyset_pagination(tmp_path: Path) -> None:
    admin_url, reader_url = require_database_urls()
    lower = datetime(2098, 1, 1, 4, 0, tzinfo=UTC)
    tied = datetime(2098, 1, 1, 4, 30, tzinfo=UTC)
    upper = datetime(2098, 1, 1, 5, 0, tzinfo=UTC)

    delete_test_rows(admin_url)
    with psycopg.connect(admin_url) as connection, connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO public.articles (
                id, title, content, author_id,
                created_at, updated_at, deleted_at
            )
            VALUES (%s, %s, 'extraction test', 1, %s, %s, %s)
            """,
            [
                (TEST_IDS[0], "lower boundary", lower, lower, None),
                (TEST_IDS[1], "timestamp tie A", lower, tied, tied),
                (TEST_IDS[2], "timestamp tie B", lower, tied, None),
                (TEST_IDS[3], "upper boundary", lower, upper, None),
            ],
        )

    try:
        result = extract_articles(
            database_url=reader_url,
            window_start=lower,
            window_end=upper,
            output_dir=tmp_path,
            rows_per_file=1,
            execution_type="SCHEDULED",
        )

        attempt_path = Path(result["attempt_path"])
        manifest = json.loads((attempt_path / "_manifest.json").read_text(encoding="utf-8"))
        part_paths = [attempt_path / part["path"] for part in manifest["parts"]]
        rows = [
            row for part_path in part_paths for row in pq.ParquetFile(part_path).read().to_pylist()
        ]

        assert [row["id"] for row in rows] == list(TEST_IDS[:3])
        assert rows[1]["deleted_at"] == tied
        assert {row["_attempt_id"] for row in rows} == {manifest["attempt_id"]}
        assert {row["_extracted_at"] for row in rows} == {
            datetime.fromisoformat(manifest["extracted_at"].replace("Z", "+00:00"))
        }
        ingest_date = datetime.fromisoformat(manifest["extracted_at"].replace("Z", "+00:00")).date()
        assert attempt_path.parts[-5:-1] == (
            "source=postgres",
            "entity=articles",
            f"ingest_date={ingest_date.isoformat()}",
            f"window={manifest['batch_id']}",
        )
        assert attempt_path.name == f"attempt={manifest['attempt_id']}"
        assert manifest["execution_type"] == "SCHEDULED"
        assert manifest["row_count"] == 3
        assert manifest["part_count"] == 3
        assert all(
            hashlib.sha256(path.read_bytes()).hexdigest() == part["sha256"]
            for path, part in zip(part_paths, manifest["parts"], strict=True)
        )
    finally:
        delete_test_rows(admin_url)


@pytest.mark.integration
def test_extract_writes_manifest_for_empty_window(tmp_path: Path) -> None:
    _, reader_url = require_database_urls()

    result = extract_articles(
        database_url=reader_url,
        window_start=datetime(2199, 1, 1, tzinfo=UTC),
        window_end=datetime(2199, 1, 1, 1, tzinfo=UTC),
        output_dir=tmp_path,
        rows_per_file=10,
        execution_type="BACKFILL",
    )

    manifest_path = Path(result["attempt_path"]) / "_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["row_count"] == 0
    assert manifest["part_count"] == 0
    assert manifest["parts"] == []
