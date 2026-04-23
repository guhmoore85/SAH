-- Staging: GA4 web analytics — one row per date × dimension value
-- Deduped via ROW_NUMBER in case airtable_active_365d has overlapping rebuilds

with deduped as (
    select *,
        row_number() over (
            partition by date, dimension_type, dimension_value
            order by date desc
        ) as rn
    from {{ source('google_analytics_4', 'airtable_active_365d') }}
    where date is not null
)

select
    -- Identity
    date,
    'Website'                       as channel,
    'Web'                           as channel_group,
    dimension_type                  as item_type,
    cast(null as string)            as item_id,
    case
        when dimension_type = 'Page'
        then concat('https://stopaapihate.org', dimension_value)
        else null
    end                             as item_url,
    dimension_value                 as item_name,
    cast(null as string)            as content_type,

    -- Date parts
    year,
    month,
    month_name,

    -- Universal metrics
    coalesce(users, 0)              as reach_or_impressions,
    coalesce(sessions, 0)           as engagement,
    round(engagement_rate * 100, 2) as engagement_rate,
    coalesce(key_events, 0)         as clicks,
    cast(null as float64)           as revenue,
    cast(null as float64)           as contributions,

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

    -- GA4-specific
    users                           as website_users,
    new_users                       as website_new_users,
    sessions                        as website_sessions,
    engagement_rate                 as website_engagement_rate,
    key_events                      as website_key_events,
    dimension_type                  as ga4_dimension_type,

    -- Email-specific (null)
    cast(null as int64)             as recipients,
    cast(null as float64)           as open_rate,
    cast(null as float64)           as click_rate,
    cast(null as float64)           as bounce_rate,
    cast(null as float64)           as unsubscribe_rate,
    cast(null as string)            as recipient_list,
    cast(null as string)            as excluded_list,

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
