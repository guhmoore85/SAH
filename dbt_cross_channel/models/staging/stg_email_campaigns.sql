-- Staging: EveryAction email campaigns — deduped to latest snapshot per email
-- Note: engagement = recipients × open_rate (rate stored as decimal 0–1)

with deduped as (
    select *,
        row_number() over (
            partition by email_name
            order by _import_timestamp desc
        ) as rn
    from {{ source('everyaction_reports', 'email_comparison') }}
    where last_sent_date is not null
)

select
    -- Identity
    last_sent_date                  as date,
    'Email'                         as channel,
    'EveryAction'                   as channel_group,
    'Email'                         as item_type,
    cast(null as string)            as item_id,
    cast(null as string)            as item_url,
    email_name                      as item_name,
    cast(null as string)            as content_type,

    -- Date parts
    extract(year from last_sent_date)           as year,
    extract(month from last_sent_date)          as month,
    format_date('%B', last_sent_date)           as month_name,

    -- Universal metrics
    safe_cast(recipients as int64)              as reach_or_impressions,
    cast(
        coalesce(safe_cast(recipients as float64), 0)
        * coalesce(unique_open_rate, 0)
        as int64
    )                                           as engagement,
    unique_open_rate                            as engagement_rate,
    cast(
        coalesce(safe_cast(recipients as float64), 0)
        * coalesce(unique_click_rate, 0)
        as int64
    )                                           as clicks,
    coalesce(amount_raised, 0)                  as revenue,
    coalesce(total_contributions, 0)            as contributions,

    -- Social-specific (null)
    cast(null as int64)             as likes,
    cast(null as int64)             as comments,
    cast(null as int64)             as shares,
    cast(null as int64)             as saves,
    cast(null as int64)             as video_views,
    cast(null as int64)             as social_impressions,
    cast(null as int64)             as new_followers,
    cast(null as int64)             as total_followers,
    cast(null as float64)           as avg_watch_time_seconds,
    cast(null as float64)           as completion_rate_pct,
    cast(null as float64)           as peak_retention_pct,

    -- GA4-specific (null)
    cast(null as int64)             as website_users,
    cast(null as int64)             as website_new_users,
    cast(null as int64)             as website_sessions,
    cast(null as float64)           as website_engagement_rate,
    cast(null as int64)             as website_key_events,
    cast(null as string)            as ga4_dimension_type,

    -- Email-specific
    safe_cast(recipients as int64)  as recipients,
    unique_open_rate                as open_rate,
    unique_click_rate               as click_rate,
    bounce_rate,
    unsubscribe_rate,
    recipient_list,
    excluded_list,

    -- Form-specific (null)
    cast(null as int64)             as form_submissions,
    cast(null as int64)             as form_views,
    cast(null as float64)           as form_conversion_rate,
    cast(null as int64)             as new_contacts,
    cast(null as int64)             as recurring_commitments,
    cast(null as string)            as form_type,
    cast(null as string)            as form_status

from deduped
where rn = 1
