"""Reusable argparse value parsers for source-simulator commands."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime


def parse_utc_timestamp(value: str) -> datetime:
    """Parse an offset-aware ISO-8601 timestamp and normalize it to UTC."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"invalid ISO-8601 timestamp: {value}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError(
            "timestamp must include a timezone, e.g. 2026-01-01T00:00:00Z"
        )
    return parsed.astimezone(UTC)


def positive_int(value: str) -> int:
    """Parse a strictly positive integer."""
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("value must be an integer") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed


def non_negative_int(value: str) -> int:
    """Parse an integer greater than or equal to zero."""
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("value must be an integer") from error
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be at least 0")
    return parsed


def positive_float(value: str) -> float:
    """Parse a strictly positive floating-point value."""
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("value must be a number") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than 0")
    return parsed
