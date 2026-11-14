import argparse
import random
from datetime import UTC, datetime, timedelta

import pytest

from demo_source.cli_types import parse_utc_timestamp
from demo_source.generate_articles import build_article


def test_build_article_is_deterministic_for_the_same_seed() -> None:
    start = datetime(2016, 1, 1, tzinfo=UTC)
    end = datetime(2026, 11, 13, tzinfo=UTC)

    first = build_article(2_000_000, random.Random(2026), start, end)
    second = build_article(2_000_000, random.Random(2026), start, end)

    assert first == second


def test_generated_article_respects_timestamp_invariants() -> None:
    start = datetime(2016, 1, 1, tzinfo=UTC)
    end = datetime(2026, 11, 13, tzinfo=UTC)
    row = build_article(2_000_000, random.Random(7), start, end)

    published_at = row[3]
    created_at = row[5]
    updated_at = row[6]
    deleted_at = row[7]

    assert start <= created_at < end
    assert created_at <= updated_at < end
    if published_at is not None:
        assert created_at <= published_at < end
        assert updated_at >= published_at
    if deleted_at is not None:
        assert created_at <= deleted_at < end
        assert updated_at == deleted_at


@pytest.mark.parametrize("seed", range(25))
def test_generated_lifecycle_stays_inside_a_short_requested_interval(seed: int) -> None:
    start = datetime(2026, 11, 13, tzinfo=UTC)
    end = start + timedelta(seconds=5)

    row = build_article(2_000_000 + seed, random.Random(seed), start, end)

    for timestamp in (row[3], row[5], row[6], row[7]):
        if timestamp is not None:
            assert start <= timestamp < end


def test_parse_timestamp_normalizes_to_utc() -> None:
    parsed = parse_utc_timestamp("2026-11-13T07:00:00+07:00")

    assert parsed == datetime(2026, 11, 13, 0, 0, tzinfo=UTC)


def test_parse_timestamp_rejects_naive_value() -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        parse_utc_timestamp("2026-11-13T00:00:00")
