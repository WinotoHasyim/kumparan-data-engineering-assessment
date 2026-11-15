select
    date(published_at) as published_date,
    author_id,
    count(*) as active_published_article_count,
    max(updated_at) as latest_article_update_at
from {{ ref('articles_active') }}
where published_at is not null
group by 1, 2
