-- Replaces the BigQuery scheduled query that built google_analytics_4.airtable_active_365d.
-- Rebuilds the same shape (one row per date × dimension_type × dimension_value)
-- straight from the Fivetran GA4 report tables.
--
-- Fixes vs. the old scheduled query:
--   - Runs after Fivetran's daily sync (dbt-cross-channel.yml), so the most
--     recent days aren't frozen at a pre-sync snapshot
--   - Each dimension's long tail is kept as one '(other)' row per day instead
--     of being dropped, so Device, Location, Campaign each total to the site
--   - Sessions are derived as engaged_sessions / engagement_rate where the
--     Fivetran report has no sessions column (GA4 defines engagement rate
--     that way); null when engagement_rate is 0 and sessions can't be known
--
-- Reading it: each dimension_type is a complete, separate breakdown of the
-- same traffic. Never sum across dimension_types (that's 3-4x the real
-- number). For site totals use dimension_type = 'Device'. Users don't add up
-- across Page rows (one person viewing 3 pages is in 3 rows) or across days.

{{ config(materialized='table') }}

{% set top_n = {'Page': 40, 'Location': 25, 'Campaign': 25} %}

with device as (
    select
        date,
        'Device'                                                as dimension_type,
        device_category                                         as dimension_value,
        total_users                                             as users,
        new_users,
        engaged_sessions,
        cast(round(safe_divide(engaged_sessions, nullif(engagement_rate, 0))) as int64) as sessions,
        cast(key_events as int64)                               as key_events,
        cast(null as int64)                                     as user_engagement_duration
    from {{ source('google_analytics_4', 'tech_device_category_report') }}
),

location as (
    select
        date,
        'Location'                                              as dimension_type,
        city                                                    as dimension_value,
        total_users                                             as users,
        new_users,
        engaged_sessions,
        cast(round(safe_divide(engaged_sessions, nullif(engagement_rate, 0))) as int64) as sessions,
        cast(key_events as int64)                               as key_events,
        cast(null as int64)                                     as user_engagement_duration
    from {{ source('google_analytics_4', 'demographic_city_report') }}
),

page as (
    -- pages_path_report has no session or engagement-rate metrics
    select
        date,
        'Page'                                                  as dimension_type,
        page_path                                               as dimension_value,
        total_users                                             as users,
        new_users,
        cast(null as int64)                                     as engaged_sessions,
        cast(null as int64)                                     as sessions,
        cast(key_events as int64)                               as key_events,
        cast(user_engagement_duration as int64)                 as user_engagement_duration
    from {{ source('google_analytics_4', 'pages_path_report') }}
),

campaign as (
    -- Fivetran stopped syncing this report on 2026-06-08; rows resume
    -- automatically once it's re-enabled in the connector
    select
        date,
        'Campaign'                                              as dimension_type,
        session_campaign_name                                   as dimension_value,
        total_users                                             as users,
        cast(null as int64)                                     as new_users,
        engaged_sessions,
        sessions,
        cast(key_events as int64)                               as key_events,
        cast(user_engagement_duration as int64)                 as user_engagement_duration
    from {{ source('google_analytics_4', 'traffic_acquisition_session_campaign_report') }}
),

unioned as (
    select * from device
    union all select * from location
    union all select * from page
    union all select * from campaign
),

ranked as (
    select
        *,
        row_number() over (
            partition by date, dimension_type
            order by users desc, dimension_value
        ) as rank_in_day
    from unioned
    where date >= date_sub(current_date('America/Los_Angeles'), interval 365 day)
),

bucketed as (
    select
        date,
        dimension_type,
        case
            {% for dim, n in top_n.items() %}
            when dimension_type = '{{ dim }}' and rank_in_day > {{ n }} then '(other)'
            {% endfor %}
            else dimension_value
        end                                                     as dimension_value,
        users,
        new_users,
        engaged_sessions,
        sessions,
        key_events,
        user_engagement_duration
    from ranked
),

aggregated as (
    select
        date,
        dimension_type,
        dimension_value,
        sum(users)                                              as users,
        sum(new_users)                                          as new_users,
        sum(sessions)                                           as sessions,
        round(safe_divide(sum(engaged_sessions), sum(sessions)), 4) as engagement_rate,
        sum(key_events)                                         as key_events,
        sum(user_engagement_duration)                           as user_engagement_duration
    from bucketed
    group by 1, 2, 3
)

select
    date,
    dimension_type,
    dimension_value,
    users,
    new_users,
    sessions,
    engagement_rate,
    key_events,
    user_engagement_duration,
    extract(year from date)                                     as year,
    extract(month from date)                                    as month,
    extract(isoweek from date)                                  as week_of_year,
    format_date('%B', date)                                     as month_name,
    format_date('%A', date)                                     as day_of_week,
    date_diff(current_date('America/Los_Angeles'), date, day)   as days_ago
from aggregated
