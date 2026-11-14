import os
from datetime import UTC, datetime

import psycopg
import pytest
from psycopg.rows import dict_row

from demo_source.mutate_articles import (
    hard_delete_article,
    insert_article,
    soft_delete_article,
    update_article,
)

ADMIN_URL = os.environ.get("SOURCE_ADMIN_DATABASE_URL")
READER_URL = os.environ.get("SOURCE_READER_DATABASE_URL")


def connect_or_skip(database_url: str | None) -> psycopg.Connection:
    if not database_url:
        pytest.skip("database URL is not configured for integration tests")
    try:
        return psycopg.connect(database_url, row_factory=dict_row)
    except psycopg.OperationalError as error:
        pytest.skip(f"local PostgreSQL source is unavailable: {error}")


@pytest.mark.integration
def test_seed_contains_required_assessment_cases() -> None:
    with connect_or_skip(ADMIN_URL) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                count(*) AS total_rows,
                count(*) FILTER (WHERE created_at < '2017-01-01') AS historical_rows,
                count(*) FILTER (WHERE published_at IS NULL) AS draft_rows,
                count(*) FILTER (WHERE deleted_at IS NOT NULL) AS soft_deleted_rows
            FROM public.articles
            WHERE id BETWEEN 1000001 AND 1000010
            """
        )
        summary = cursor.fetchone()

    assert summary == {
        "total_rows": 10,
        "historical_rows": 1,
        "draft_rows": 1,
        "soft_deleted_rows": 1,
    }


@pytest.mark.integration
def test_seed_contains_pagination_timestamp_tie() -> None:
    with connect_or_skip(ADMIN_URL) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT updated_at, array_agg(id ORDER BY id) AS article_ids
            FROM public.articles
            WHERE id IN (1000005, 1000006)
            GROUP BY updated_at
            """
        )
        row = cursor.fetchone()

    assert row["article_ids"] == [1000005, 1000006]


@pytest.mark.integration
def test_seed_covers_every_year_in_the_backfill_range() -> None:
    with connect_or_skip(ADMIN_URL) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT array_agg(seed_year ORDER BY seed_year) AS years
            FROM (
                SELECT DISTINCT extract(year FROM created_at)::int AS seed_year
                FROM public.articles
                WHERE id BETWEEN 1000001 AND 1102025
            ) AS yearly_seed
            """
        )
        years = cursor.fetchone()["years"]

    assert years == list(range(2016, 2027))


@pytest.mark.integration
def test_source_reader_can_select_but_cannot_insert() -> None:
    with connect_or_skip(READER_URL) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT count(*) AS row_count FROM public.articles")
        assert cursor.fetchone()["row_count"] >= 10

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cursor.execute(
                """
                INSERT INTO public.articles (title, content, author_id)
                VALUES ('forbidden', 'forbidden', 1)
                """
            )


@pytest.mark.integration
def test_source_trigger_supports_normal_and_defensive_delete_scenarios() -> None:
    test_id = 9_000_001
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    boundary_at = datetime(2026, 11, 13, 3, 0, tzinfo=UTC)

    with connect_or_skip(ADMIN_URL) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO public.articles (
                id, title, content, author_id, created_at, updated_at
            )
            VALUES (%s, 'trigger test', 'trigger test', 1, %s, %s)
            RETURNING updated_at
            """,
            (test_id, created_at, created_at),
        )
        initial_updated_at = cursor.fetchone()["updated_at"]

        cursor.execute(
            """
            UPDATE public.articles
            SET title = 'normal update'
            WHERE id = %s
            RETURNING updated_at
            """,
            (test_id,),
        )
        normal_updated_at = cursor.fetchone()["updated_at"]
        assert normal_updated_at > initial_updated_at

        cursor.execute(
            """
            UPDATE public.articles
            SET updated_at = %s
            WHERE id = %s
            RETURNING updated_at
            """,
            (boundary_at, test_id),
        )
        assert cursor.fetchone()["updated_at"] == boundary_at

        cursor.execute("SET LOCAL app.preserve_updated_at = 'on'")
        cursor.execute(
            """
            UPDATE public.articles
            SET deleted_at = %s
            WHERE id = %s
            RETURNING updated_at, deleted_at
            """,
            (boundary_at, test_id),
        )
        soft_deleted = cursor.fetchone()
        assert soft_deleted["updated_at"] == boundary_at
        assert soft_deleted["deleted_at"] == boundary_at

        connection.rollback()


@pytest.mark.integration
def test_mutation_service_commits_insert_update_soft_and_hard_delete() -> None:
    if not ADMIN_URL:
        pytest.skip("SOURCE_ADMIN_DATABASE_URL is not configured for integration tests")

    article_id = 9_100_001
    now = datetime.now(UTC)
    with psycopg.connect(ADMIN_URL) as connection, connection.cursor() as cursor:
        cursor.execute("DELETE FROM public.articles WHERE id = %s", (article_id,))

    inserted = insert_article(
        ADMIN_URL,
        article_id=article_id,
        title="Mutation service integration test",
        content="Controlled test data",
        author_id=1,
        published_at=now,
        created_at=now,
        updated_at=now,
    )
    assert inserted["id"] == article_id

    updated = update_article(ADMIN_URL, article_id, title="Updated integration test")
    assert updated["title"] == "Updated integration test"
    assert updated["updated_at"] >= now

    soft_deleted = soft_delete_article(ADMIN_URL, article_id)
    assert soft_deleted["deleted_at"] is not None

    hard_deleted = hard_delete_article(ADMIN_URL, article_id)
    assert hard_deleted["id"] == article_id

    with psycopg.connect(ADMIN_URL) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM public.articles WHERE id = %s", (article_id,))
        assert cursor.fetchone()[0] == 0
