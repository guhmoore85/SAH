-- Staging: EveryAction forms — deduped to latest snapshot per form

with deduped as (
    select *,
        row_number() over (
            partition by form_name
            order by _fivetran_synced desc
        ) as rn
    from {{ source('everyaction_reports', 'forms_report') }}
    where last_submission_date is not null
)

select
    -- Identity
    last_submission_date                        as date,
    'Forms'                                     as channel,
    'EveryAction'                               as channel_group,
    'Form'                                      as item_type,
    cast(null as string)                        as item_id,
    cast(null as string)                        as item_url,
    form_name                                   as item_name,
    form_type                                   as content_type,

    -- Date parts
    extract(year from last_submission_date)     as year,
    extract(month from last_submission_date)    as month,
    format_date('%B', last_submission_date)     as month_name,

    -- Universal metrics
    safe_cast(number_of_views as int64)         as reach_or_impressions,
    safe_cast(number_of_submissions as int64)   as engagement,
    conversion_rate                             as engagement_rate,
    cast(null as int64)                         as clicks,
    coalesce(total_contribution_amount, 0)      as revenue,
    coalesce(number_of_contributions, 0)        as contributions,

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

    -- Email-specific (null)
    cast(null as int64)             as recipients,
    cast(null as float64)           as open_rate,
    cast(null as float64)           as click_rate,
    cast(null as float64)           as bounce_rate,
    cast(null as float64)           as unsubscribe_rate,
    cast(null as string)            as recipient_list,
    cast(null as string)            as excluded_list,

    -- Form-specific
    safe_cast(number_of_submissions as int64)           as form_submissions,
    safe_cast(number_of_views as int64)                 as form_views,
    conversion_rate                                     as form_conversion_rate,
    safe_cast(number_of_new_contacts as int64)          as new_contacts,
    safe_cast(number_of_recurring_commitments as int64) as recurring_commitments,
    form_type,
    form_status

from deduped
where rn = 1
