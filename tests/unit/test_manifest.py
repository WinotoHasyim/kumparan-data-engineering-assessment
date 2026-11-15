import json

import pytest

from ingestion.manifest import resolve_manifest, validate_manifest

MANIFEST_URI = (
    "gs://test-bucket/raw/source=postgres/entity=articles/ingest_date=2026-11-15/"
    "window=updated_at_20261115T010000Z_20261115T020000Z/attempt=abc/_manifest.json"
)


def build_manifest(*, row_count: int = 3) -> dict:
    prefix = MANIFEST_URI.rsplit("/", 1)[0]
    parts = []
    if row_count:
        parts = [
            {
                "object": f"{prefix.removeprefix('gs://test-bucket/')}/part-00000.parquet",
                "uri": f"{prefix}/part-00000.parquet",
                "row_count": row_count,
                "size_bytes": 2048,
                "crc32c": "test-crc32c",
                "generation": 123,
            }
        ]
    return {
        "manifest_version": 1,
        "source": "postgres",
        "entity": "articles",
        "attempt_id": "abc",
        "batch_id": "updated_at_20261115T010000Z_20261115T020000Z",
        "cursor_column": "updated_at",
        "part_count": len(parts),
        "row_count": row_count,
        "parts": parts,
    }


class FakeBlob:
    def __init__(self, object_name: str, payload: dict, *, generation: int = 123) -> None:
        self.object_name = object_name
        self.payload = payload
        self.generation = str(generation)
        self.crc32c = "test-crc32c"
        self.size = 2048

    def download_as_bytes(self) -> bytes:
        return json.dumps(self.payload).encode()

    def reload(self) -> None:
        pass


class FakeBucket:
    def __init__(self, payload: dict, *, generation: int = 123) -> None:
        self.payload = payload
        self.generation = generation

    def blob(self, object_name: str) -> FakeBlob:
        return FakeBlob(object_name, self.payload, generation=self.generation)


class FakeStorageClient:
    def __init__(self, payload: dict, *, generation: int = 123) -> None:
        self.payload = payload
        self.generation = generation

    def bucket(self, _: str) -> FakeBucket:
        return FakeBucket(self.payload, generation=self.generation)


def test_resolves_exact_manifest_parts() -> None:
    manifest = build_manifest()

    result = resolve_manifest(
        manifest_uri=MANIFEST_URI,
        storage_client=FakeStorageClient(manifest),
    )

    assert result == {
        "attempt_id": "abc",
        "batch_id": "updated_at_20261115T010000Z_20261115T020000Z",
        "cursor_column": "updated_at",
        "manifest_uri": MANIFEST_URI,
        "part_uris": [manifest["parts"][0]["uri"]],
        "row_count": 3,
    }


def test_empty_manifest_resolves_to_no_parts() -> None:
    result = resolve_manifest(
        manifest_uri=MANIFEST_URI,
        storage_client=FakeStorageClient(build_manifest(row_count=0)),
    )

    assert result["part_uris"] == []
    assert result["row_count"] == 0


def test_rejects_manifest_count_mismatch() -> None:
    manifest = build_manifest()
    manifest["row_count"] = 4

    with pytest.raises(ValueError, match="sum of part rows"):
        validate_manifest(manifest, MANIFEST_URI)


@pytest.mark.parametrize("field", ["row_count", "part_count"])
def test_rejects_boolean_manifest_counts(field: str) -> None:
    manifest = build_manifest()
    manifest[field] = True

    with pytest.raises(ValueError, match=field):
        validate_manifest(manifest, MANIFEST_URI)


def test_rejects_missing_batch_identity() -> None:
    manifest = build_manifest()
    del manifest["attempt_id"]

    with pytest.raises(ValueError, match="attempt_id"):
        validate_manifest(manifest, MANIFEST_URI)


def test_rejects_batch_identity_for_another_cursor() -> None:
    manifest = build_manifest()
    manifest["cursor_column"] = "created_at"

    with pytest.raises(ValueError, match="batch_id"):
        validate_manifest(manifest, MANIFEST_URI)


def test_rejects_part_outside_manifest_attempt() -> None:
    manifest = build_manifest()
    manifest["parts"][0]["object"] = "other/part-00000.parquet"
    manifest["parts"][0]["uri"] = "gs://test-bucket/other/part-00000.parquet"

    with pytest.raises(ValueError, match="colocated"):
        validate_manifest(manifest, MANIFEST_URI)


def test_rejects_object_generation_that_differs_from_manifest() -> None:
    with pytest.raises(ValueError, match="generation does not match"):
        resolve_manifest(
            manifest_uri=MANIFEST_URI,
            storage_client=FakeStorageClient(build_manifest(), generation=456),
        )
