"""Resolve and validate one committed article extraction manifest."""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

from google.cloud import storage

GCP_PROJECT_ID_ENV = "GCP_PROJECT_ID"
EXPECTED_MANIFEST_VERSION = 1
EXPECTED_SOURCE = "postgres"
EXPECTED_ENTITY = "articles"
EXPECTED_CURSOR_COLUMNS = ("created_at", "updated_at")


def parse_gcs_uri(uri: str) -> tuple[str, str]:
    """Split one fully qualified GCS object URI into bucket and object names."""
    if not uri.startswith("gs://"):
        raise ValueError(f"GCS URI must start with gs://: {uri}")
    bucket_name, separator, object_name = uri.removeprefix("gs://").partition("/")
    if not separator or not bucket_name or not object_name:
        raise ValueError(f"GCS URI must identify one object: {uri}")
    return bucket_name, object_name


def read_manifest(storage_client: storage.Client, manifest_uri: str) -> dict[str, Any]:
    """Read and decode one immutable GCS manifest."""
    bucket_name, object_name = parse_gcs_uri(manifest_uri)
    payload = storage_client.bucket(bucket_name).blob(object_name).download_as_bytes()
    manifest = json.loads(payload)
    if not isinstance(manifest, dict):
        raise ValueError("manifest must contain one JSON object")
    return manifest


def validate_manifest(manifest: dict[str, Any], manifest_uri: str) -> list[str]:
    """Validate manifest identity, counts, and exact colocated Parquet objects."""
    expected_values = {
        "manifest_version": EXPECTED_MANIFEST_VERSION,
        "source": EXPECTED_SOURCE,
        "entity": EXPECTED_ENTITY,
    }
    for field, expected in expected_values.items():
        if manifest.get(field) != expected:
            raise ValueError(f"manifest {field} must be {expected!r}")

    for field in ("attempt_id", "batch_id"):
        if not isinstance(manifest.get(field), str) or not manifest[field]:
            raise ValueError(f"manifest {field} must be a non-empty string")

    cursor_column = manifest.get("cursor_column")
    if cursor_column not in EXPECTED_CURSOR_COLUMNS:
        allowed = ", ".join(EXPECTED_CURSOR_COLUMNS)
        raise ValueError(f"manifest cursor_column must be one of: {allowed}")
    if not manifest["batch_id"].startswith(f"{cursor_column}_"):
        raise ValueError("manifest batch_id must identify its cursor column")

    manifest_row_count = manifest.get("row_count")
    part_count = manifest.get("part_count")
    if (
        not isinstance(manifest_row_count, int)
        or isinstance(manifest_row_count, bool)
        or manifest_row_count < 0
    ):
        raise ValueError("manifest row_count must be a non-negative integer")
    if not isinstance(part_count, int) or isinstance(part_count, bool) or part_count < 0:
        raise ValueError("manifest part_count must be a non-negative integer")

    parts = manifest.get("parts")
    if not isinstance(parts, list):
        raise ValueError("manifest parts must be a list")
    if part_count != len(parts):
        raise ValueError("manifest part_count does not match parts")

    manifest_bucket, manifest_object = parse_gcs_uri(manifest_uri)
    if not manifest_object.endswith("/_manifest.json"):
        raise ValueError("manifest URI must identify an _manifest.json completion marker")
    expected_object_prefix = f"{manifest_object.rsplit('/', 1)[0]}/"
    expected_uri_prefix = f"gs://{manifest_bucket}/{expected_object_prefix}"
    part_uris: list[str] = []
    part_rows = 0
    for part in parts:
        if not isinstance(part, dict):
            raise ValueError("every manifest part must be an object")
        object_name = part.get("object")
        uri = part.get("uri")
        part_row_count = part.get("row_count")
        size_bytes = part.get("size_bytes")
        crc32c = part.get("crc32c")
        generation = part.get("generation")
        if not isinstance(object_name, str) or not object_name.startswith(expected_object_prefix):
            raise ValueError("every part object must be colocated with its manifest")
        if uri != f"gs://{manifest_bucket}/{object_name}" or not uri.startswith(
            expected_uri_prefix
        ):
            raise ValueError("every part URI must be colocated with its manifest")
        if (
            not isinstance(part_row_count, int)
            or isinstance(part_row_count, bool)
            or part_row_count < 1
        ):
            raise ValueError("every part row_count must be a positive integer")
        if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 1:
            raise ValueError("every part size_bytes must be a positive integer")
        if not isinstance(crc32c, str) or not crc32c:
            raise ValueError("every part crc32c must be a non-empty string")
        if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
            raise ValueError("every part generation must be a positive integer")
        parse_gcs_uri(uri)
        part_uris.append(uri)
        part_rows += part_row_count

    if len(set(part_uris)) != len(part_uris):
        raise ValueError("manifest contains duplicate part URIs")
    if manifest_row_count != part_rows:
        raise ValueError("manifest row_count does not equal the sum of part rows")
    if not parts and manifest_row_count != 0:
        raise ValueError("an empty manifest must have row_count 0")
    return part_uris


def verify_part_objects(
    storage_client: storage.Client,
    manifest: dict[str, Any],
) -> None:
    """Verify that every live GCS object still matches its manifest metadata."""
    for part in manifest["parts"]:
        bucket_name, object_name = parse_gcs_uri(part["uri"])
        blob = storage_client.bucket(bucket_name).blob(object_name)
        blob.reload()

        actual_generation = int(blob.generation) if blob.generation is not None else None
        if actual_generation != part["generation"]:
            raise ValueError(f"part generation does not match manifest: {part['uri']}")
        if blob.crc32c != part["crc32c"]:
            raise ValueError(f"part crc32c does not match manifest: {part['uri']}")
        if blob.size != part["size_bytes"]:
            raise ValueError(f"part size_bytes does not match manifest: {part['uri']}")


def resolve_manifest(*, manifest_uri: str, storage_client: storage.Client) -> dict[str, Any]:
    """Return the validated metadata required by a downstream load task."""
    manifest = read_manifest(storage_client, manifest_uri)
    part_uris = validate_manifest(manifest, manifest_uri)
    verify_part_objects(storage_client, manifest)
    return {
        "attempt_id": manifest["attempt_id"],
        "batch_id": manifest["batch_id"],
        "cursor_column": manifest["cursor_column"],
        "manifest_uri": manifest_uri,
        "part_uris": part_uris,
        "row_count": manifest["row_count"],
    }


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line interface."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-uri", required=True)
    parser.add_argument("--gcp-project-id", help=f"Override {GCP_PROJECT_ID_ENV}")
    return parser


def main() -> None:
    """Resolve one manifest from command-line arguments."""
    args = build_parser().parse_args()
    project_id = args.gcp_project_id or os.environ.get(GCP_PROJECT_ID_ENV)
    result = resolve_manifest(
        manifest_uri=args.manifest_uri,
        storage_client=storage.Client(project=project_id),
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
