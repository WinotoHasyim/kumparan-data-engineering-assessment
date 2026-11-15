{{
    config(
        materialized='incremental',
        unique_key='id',
        incremental_strategy='merge',
        partition_by={"field": "updated_at", "data_type": "timestamp", "granularity": "day"},
        cluster_by=['id', 'author_id'],
        on_schema_change='fail'
    )
}}

with observations as (
    select *
    from {{ ref('stg_articles') }}
    {% if is_incremental() %}
    where _extracted_at >= (
        select coalesce(max(_extracted_at), timestamp('1970-01-01'))
        from {{ this }}
    )
    {% endif %}
),

latest_candidates as (
    select *
    from observations
    qualify row_number() over (
        partition by id
        order by updated_at desc, _extracted_at desc, _attempt_id desc
    ) = 1
)

select candidate.*
from latest_candidates as candidate
{% if is_incremental() %}
left join {{ this }} as existing using (id)
where existing.id is null
   or candidate.updated_at > existing.updated_at
   or (
       candidate.updated_at = existing.updated_at
       and candidate._extracted_at > existing._extracted_at
   )
   or (
       candidate.updated_at = existing.updated_at
       and candidate._extracted_at = existing._extracted_at
       and candidate._attempt_id > existing._attempt_id
   )
{% endif %}
