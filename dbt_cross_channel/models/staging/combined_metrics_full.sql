-- Replaces the deleted BigQuery scheduled query that built social_media.combined_metrics_full.
-- Unions Fivetran Facebook and Instagram source tables into a single social metrics table.

{{ config(materialized='table') }}

with facebook as (
    select
        cast(date_day as date)                          as date,
        'Facebook'                                      as platform,
        'Post'                                          as content_type,
        page_name                                       as account_name,
        cast(page_id as string)                         as page_id,
        cast(post_id as string)                         as post_id,
        post_message                                    as content,
        post_url,
        cast(created_timestamp as date)                 as post_created_at,

        -- Engagement metrics
        cast(likes as int64)                            as likes,
        cast(clicks as int64)                           as clicks,
        cast(null as int64)                             as comments,
        cast(null as int64)                             as shares,
        cast(null as int64)                             as saves,
        cast(impressions as int64)                      as impressions,
        cast(null as int64)                             as reach,
        cast(media_views as int64)                      as media_views,
        cast(video_views as int64)                      as video_views,

        -- Video specific
        cast(video_avg_time_watched as float64)         as video_avg_time_watched,
        cast(video_view_time as float64)                as video_view_time,
        cast(video_views_10s as int64)                  as video_views_10s,
        cast(video_views_15s as int64)                  as video_views_15s,

        cast(is_most_recent_record as bool)             as is_most_recent_record,
        source_relation

    from {{ source('fivetran_facebook', 'facebook_pages__posts_report') }}
),

instagram as (
    select
        cast(created_timestamp as date)                 as date,
        'Instagram'                                     as platform,
        case
            when media_type in ('VIDEO', 'REEL') then 'Video'
            when media_type = 'CAROUSEL_ALBUM' then 'Carousel'
            when media_type = 'IMAGE' then 'Image'
            else 'Unknown'
        end                                             as content_type,
        account_name,
        cast(user_id as string)                         as page_id,
        cast(post_id as string)                         as post_id,
        post_caption                                    as content,
        post_url,
        cast(created_timestamp as date)                 as post_created_at,

        -- Engagement metrics
        cast(like_count as int64)                       as likes,
        cast(null as int64)                             as clicks,
        cast(comment_count as int64)                    as comments,
        cast(null as int64)                             as shares,
        cast(null as int64)                             as saves,
        cast(coalesce(carousel_album_impressions, story_impressions, video_photo_impressions) as int64) as impressions,
        cast(null as int64)                             as reach,
        cast(null as int64)                             as media_views,
        cast(coalesce(video_photo_views, reel_views, carousel_album_video_views) as int64) as video_views,

        -- Video specific
        cast(null as float64)                           as video_avg_time_watched,
        cast(null as float64)                           as video_view_time,
        cast(null as int64)                             as video_views_10s,
        cast(null as int64)                             as video_views_15s,

        cast(null as bool)                              as is_most_recent_record,
        source_relation

    from {{ source('fivetran_instagram', 'instagram_business__posts') }}
),

unioned as (
    select * from facebook
    union all
    select * from instagram
),

enriched as (
    select
        date,
        platform,
        content_type,
        account_name,
        page_id,
        post_id,
        content,
        post_url,
        post_created_at,

        -- Derived date parts
        extract(year from date)                         as year,
        extract(month from date)                        as month,
        format_date('%B', date)                         as month_name,

        -- Engagement metrics
        likes,
        clicks,
        comments,
        shares,
        saves,
        impressions,
        reach,
        media_views,
        video_views,

        -- Total engagement = all interactions summed
        coalesce(likes, 0) + coalesce(comments, 0) + coalesce(shares, 0)
            + coalesce(clicks, 0) + coalesce(saves, 0)
                                                        as total_engagement,

        -- Engagement rate = total engagement / impressions
        safe_divide(
            coalesce(likes, 0) + coalesce(comments, 0) + coalesce(shares, 0)
                + coalesce(clicks, 0) + coalesce(saves, 0),
            impressions
        )                                               as engagement_rate,

        -- Video specific
        video_avg_time_watched                          as avg_watch_time_seconds,
        cast(null as float64)                           as completion_rate_pct,
        cast(null as float64)                           as peak_retention_pct,

        -- Follower metrics (not available at post level from Fivetran)
        cast(null as int64)                             as new_followers,
        cast(null as int64)                             as total_followers,

        is_most_recent_record,
        source_relation

    from unioned
)

select * from enriched
