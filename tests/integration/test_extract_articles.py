import io
import json
import os
from datetime import UTC, datetime

import psycopg
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from ingestion.extract_articles import extract_articles

ADMIN_URL = os.environ.get("SOURCE_ADMIN_DATABASE_URL")
READER_URL = os.environ.get("SOURCE_READER_DATABASE_URL")
TEST_IDS = (9_200_001, 9_200_002, 9_200_003, 9_200_004)


class FakeUpload(io.BytesIO):
    """Collect one streaming upload without contacting GCS."""

    def __init__(self, blob: "FakeBlob", *, create_only: bool) -> None:
        super().__init__()
        self.blob = blob
        self.create_only = create_only

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if exc_type is None:
            self.blob.commit(self.getvalue(), create_only=self.create_only)
        self.close()


class FakeBlob:
    """Implement only the GCS Blob operations used by the extractor."""

    def __init__(self, bucket: "FakeBucket", name: str) -> None:
        self.bucket = bucket
        self.name = name
        self.size: int | None = None
        self.crc32c: str | None = None
        self.generation: int | None = None
        self.content_type: str | None = None

    def open(self, mode: str, **kwargs: object) -> FakeUpload:
        assert mode == "wb"
        return FakeUpload(self, create_only=kwargs.get("if_generation_match") == 0)

    def upload_from_string(
        self,
        data: str,
        *,
        content_type: str,
        if_generation_match: int,
    ) -> None:
        assert content_type == "application/json"
        self.commit(data.encode(), create_only=if_generation_match == 0)

    def commit(self, data: bytes, *, create_only: bool) -> None:
        if create_only and self.name in self.bucket.objects:
            raise RuntimeError(f"object already exists: {self.name}")
        self.bucket.objects[self.name] = data
        self.bucket.write_order.append(self.name)
        self.size = len(data)
        self.crc32c = "test-crc32c"
        self.generation = len(self.bucket.write_order)

    def reload(self) -> None:
        return None


class FakeBucket:
    """Keep uploaded GCS objects in memory for integration assertions."""

    def __init__(self, name: str = "test-raw-bucket") -> None:
        self.name = name
        self.objects: dict[str, bytes] = {}
        self.write_order: list[str] = []

    def blob(self, name: str, **_: object) -> FakeBlob:
        return FakeBlob(self, name)


def require_database_urls() -> tuple[str, str]:
    if not ADMIN_URL or not READER_URL:
        pytest.skip("database URLs are not configured for integration tests")
    return ADMIN_URL, READER_URL


def delete_test_rows(admin_url: str) -> None:
    with psycopg.connect(admin_url) as connection, connection.cursor() as cursor:
        cursor.execute("DELETE FROM public.articles WHERE id = ANY(%s)", (list(TEST_IDS),))


@pytest.mark.integration
def test_extract_uses_half_open_window_and_keyset_pagination() -> None:
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
        bucket = FakeBucket()
        result = extract_articles(
            database_url=reader_url,
            window_start=lower,
            window_end=upper,
            bucket=bucket,
            gcs_prefix="raw",
            fetch_size=1,
            rows_per_file=2,
            execution_type="SCHEDULED",
            attempt_id="kestra-execution-test",
        )

        manifest_object = result["manifest_uri"].removeprefix(f"gs://{bucket.name}/")
        manifest = json.loads(bucket.objects[manifest_object])
        parquet_files = [
            pq.ParquetFile(pa.BufferReader(bucket.objects[part["object"]]))
            for part in manifest["parts"]
        ]
        rows = [row for parquet_file in parquet_files for row in parquet_file.read().to_pylist()]

        assert [row["id"] for row in rows] == list(TEST_IDS[:3])
        assert rows[1]["deleted_at"] == tied
        assert {row["_attempt_id"] for row in rows} == {manifest["attempt_id"]}
        assert {row["_extracted_at"] for row in rows} == {
            datetime.fromisoformat(manifest["extracted_at"].replace("Z", "+00:00"))
        }
        ingest_date = datetime.fromisoformat(manifest["extracted_at"].replace("Z", "+00:00")).date()
        expected_prefix = (
            "raw/source=postgres/entity=articles/"
            f"ingest_date={ingest_date.isoformat()}/"
            f"window={manifest['batch_id']}/attempt={manifest['attempt_id']}"
        )
        assert manifest_object == f"{expected_prefix}/_manifest.json"
        assert manifest["execution_type"] == "SCHEDULED"
        assert manifest["cursor_column"] == "updated_at"
        assert manifest["batch_id"].startswith("updated_at_")
        assert manifest["attempt_id"] == "kestra-execution-test"
        assert manifest["row_count"] == 3
        assert manifest["part_count"] == 2
        assert [part["row_count"] for part in manifest["parts"]] == [2, 1]
        assert [parquet_file.metadata.num_row_groups for parquet_file in parquet_files] == [2, 1]
        assert all(part["object"].startswith(expected_prefix) for part in manifest["parts"])
        assert all(
            part["uri"] == f"gs://{bucket.name}/{part['object']}" for part in manifest["parts"]
        )
        assert bucket.write_order[-1] == manifest_object
    finally:
        delete_test_rows(admin_url)


@pytest.mark.integration
def test_backfill_uses_created_at_instead_of_updated_at() -> None:
    admin_url, reader_url = require_database_urls()
    lower = datetime(2097, 1, 1, tzinfo=UTC)
    upper = datetime(2097, 2, 1, tzinfo=UTC)

    delete_test_rows(admin_url)
    with psycopg.connect(admin_url) as connection, connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO public.articles (
                id, title, content, author_id, created_at, updated_at
            )
            VALUES (%s, %s, 'backfill cursor test', 1, %s, %s)
            """,
            [
                (TEST_IDS[0], "created in window", lower, datetime(2098, 1, 1, tzinfo=UTC)),
                (
                    TEST_IDS[1],
                    "only updated in window",
                    datetime(2096, 1, 1, tzinfo=UTC),
                    lower,
                ),
            ],
        )

    try:
        bucket = FakeBucket()
        result = extract_articles(
            database_url=reader_url,
            window_start=lower,
            window_end=upper,
            bucket=bucket,
            gcs_prefix="raw",
            fetch_size=1,
            rows_per_file=1,
            execution_type="BACKFILL",
            attempt_id="backfill-created-at-test",
        )

        manifest_object = result["manifest_uri"].removeprefix(f"gs://{bucket.name}/")
        manifest = json.loads(bucket.objects[manifest_object])
        parquet_file = pq.ParquetFile(
            pa.BufferReader(bucket.objects[manifest["parts"][0]["object"]])
        )

        assert [row["id"] for row in parquet_file.read().to_pylist()] == [TEST_IDS[0]]
        assert manifest["cursor_column"] == "created_at"
        assert manifest["batch_id"].startswith("created_at_")
    finally:
        delete_test_rows(admin_url)


@pytest.mark.integration
def test_extract_writes_manifest_for_empty_window() -> None:
    _, reader_url = require_database_urls()

    bucket = FakeBucket()
    result = extract_articles(
        database_url=reader_url,
        window_start=datetime(2199, 1, 1, tzinfo=UTC),
        window_end=datetime(2199, 1, 1, 1, tzinfo=UTC),
        bucket=bucket,
        gcs_prefix="raw",
        fetch_size=3,
        rows_per_file=10,
        execution_type="BACKFILL",
    )

    manifest_object = result["manifest_uri"].removeprefix(f"gs://{bucket.name}/")
    manifest = json.loads(bucket.objects[manifest_object])

    assert manifest["row_count"] == 0
    assert manifest["part_count"] == 0
    assert manifest["cursor_column"] == "created_at"
    assert manifest["parts"] == []
    assert bucket.write_order == [manifest_object]
