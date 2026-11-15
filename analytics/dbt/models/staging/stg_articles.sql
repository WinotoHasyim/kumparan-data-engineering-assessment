with source as (
    select *
    from {{ source('landing', 'article_observations') }}
)

select
    id,
    title,
    content,
    published_at,
    author_id,
    created_at,
    updated_at,
    deleted_at,
    _attempt_id,
    _extracted_at
from source
