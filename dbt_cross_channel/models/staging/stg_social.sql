-- Staging: Social media posts
-- Source is already deduped (combined_metrics_full)

select
    -- Identity
    date,
    platform                        as channel,
    'Social'                        as channel_group,
    'Post'                          as item_type,
    post_id                         as item_id,
    post_url                        as item_url,
    left(content, 500)              as item_name,
    content_type,

    -- Date parts
    year,
    month,
    month_name,

    -- Universal metrics
    coalesce(reach, 0)              as reach_or_impressions,
    coalesce(total_engagement, 0)   as engagement,
    engagement_rate,
    coalesce(clicks, 0)             as clicks,
    cast(null as float64)           as revenue,
    cast(null as float64)           as contributions,

    -- Social-specific
    likes,
    comments,
    shares,
    saves,
    video_views,
    impressions                     as social_impressions,
    new_followers,
    total_followers,
    avg_watch_time_seconds,
    completion_rate_pct,
    peak_retention_pct,

    -- GA4-specific (null)
    cast(null as int64)             as website_users,
    cast(null as int64)             as website_new_users,
    cast(null as int64)             as website_sessions,
    cast(null as float64)           as website_engagement_rate,
    cast(null as int64)             as website_key_events,
    cast(null as string)            as ga4_dimension_type,

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

from {{ ref('combined_metrics_full') }}
where date is not null
