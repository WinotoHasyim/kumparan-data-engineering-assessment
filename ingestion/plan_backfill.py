"""Split one bounded historical interval into resumable calendar-month chunks."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime

from demo_source.cli_types import parse_utc_timestamp
from ingestion.extract_articles import utc_text


def next_month_boundary(value: datetime) -> datetime:
    """Return the first UTC instant of the following calendar month."""
    if value.month == 12:
        return datetime(value.year + 1, 1, 1, tzinfo=UTC)
    return datetime(value.year, value.month + 1, 1, tzinfo=UTC)


def plan_monthly_chunks(window_start: datetime, window_end: datetime) -> list[dict[str, str]]:
    """Return contiguous half-open monthly chunks covering exactly one interval."""
    if window_start.tzinfo is None or window_start.utcoffset() is None:
        raise ValueError("window-start must include timezone information")
    if window_end.tzinfo is None or window_end.utcoffset() is None:
        raise ValueError("window-end must include timezone information")
    if window_start >= window_end:
        raise ValueError("window-start must be before window-end")

    current = window_start.astimezone(UTC)
    end = window_end.astimezone(UTC)
    chunks: list[dict[str, str]] = []
    while current < end:
        chunk_end = min(next_month_boundary(current), end)
        chunks.append(
            {
                "window_start": utc_text(current),
                "window_end": utc_text(chunk_end),
            }
        )
        current = chunk_end
    return chunks


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line interface."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window-start", type=parse_utc_timestamp, required=True)
    parser.add_argument("--window-end", type=parse_utc_timestamp, required=True)
    return parser


def main() -> None:
    """Print a JSON backfill plan for orchestration."""
    args = build_parser().parse_args()
    result = {
        "chunks": plan_monthly_chunks(args.window_start, args.window_end),
    }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
