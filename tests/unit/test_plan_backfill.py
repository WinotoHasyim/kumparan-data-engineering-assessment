from datetime import UTC, datetime

import pytest

from ingestion.plan_backfill import plan_monthly_chunks


def test_plans_contiguous_monthly_chunks_with_partial_final_month() -> None:
    chunks = plan_monthly_chunks(
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 3, 15, 12, tzinfo=UTC),
    )

    assert chunks == [
        {
            "window_start": "2026-01-01T00:00:00Z",
            "window_end": "2026-02-01T00:00:00Z",
        },
        {
            "window_start": "2026-02-01T00:00:00Z",
            "window_end": "2026-03-01T00:00:00Z",
        },
        {
            "window_start": "2026-03-01T00:00:00Z",
            "window_end": "2026-03-15T12:00:00Z",
        },
    ]


def test_uses_calendar_boundaries_for_partial_first_and_last_months() -> None:
    chunks = plan_monthly_chunks(
        datetime(2024, 1, 31, tzinfo=UTC),
        datetime(2024, 4, 2, tzinfo=UTC),
    )

    assert chunks == [
        {
            "window_start": "2024-01-31T00:00:00Z",
            "window_end": "2024-02-01T00:00:00Z",
        },
        {
            "window_start": "2024-02-01T00:00:00Z",
            "window_end": "2024-03-01T00:00:00Z",
        },
        {
            "window_start": "2024-03-01T00:00:00Z",
            "window_end": "2024-04-01T00:00:00Z",
        },
        {
            "window_start": "2024-04-01T00:00:00Z",
            "window_end": "2024-04-02T00:00:00Z",
        },
    ]


@pytest.mark.parametrize(
    ("window_start", "window_end"),
    [
        (
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 1, tzinfo=UTC),
        ),
        (
            datetime(2026, 1, 2, tzinfo=UTC),
            datetime(2026, 1, 1, tzinfo=UTC),
        ),
    ],
)
def test_rejects_empty_or_reversed_interval(
    window_start: datetime,
    window_end: datetime,
) -> None:
    with pytest.raises(ValueError, match="before"):
        plan_monthly_chunks(window_start, window_end)


@pytest.mark.parametrize("field", ["window_start", "window_end"])
def test_rejects_naive_interval_boundaries(field: str) -> None:
    window_start = datetime(2026, 1, 1, tzinfo=UTC)
    window_end = datetime(2026, 2, 1, tzinfo=UTC)
    if field == "window_start":
        window_start = window_start.replace(tzinfo=None)
    else:
        window_end = window_end.replace(tzinfo=None)

    with pytest.raises(ValueError, match=field.replace("_", "-")):
        plan_monthly_chunks(window_start, window_end)
