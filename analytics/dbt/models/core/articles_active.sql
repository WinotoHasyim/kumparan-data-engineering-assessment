{{ config(materialized='view') }}

select *
from {{ ref('articles_current') }}
where deleted_at is null
