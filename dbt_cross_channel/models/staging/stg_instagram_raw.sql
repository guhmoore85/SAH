-- Staging: Instagram posts, built directly from the raw Fivetran-synced
-- media_history + media_insights tables.
--
-- Fivetran's own "Social Media Reporting" transformation (which used to
-- populate instagram_business__posts) is stuck ~5 months behind despite
-- media_history/media_insights themselves being fully current, so this
-- bypasses that transformation and builds the equivalent shape ourselves.
--
-- Output columns intentionally match the old instagram_business__posts
-- table so combined_metrics_full.sql only needs to swap its source, not
-- its column logic. media_product_type = 'REELS' is mapped to
-- media_type = 'REEL' to match the downstream CASE statement, which
-- expects that value -- raw media_type never actually contains 'REEL',
-- reels are identified via media_product_type instead.
--
-- Stories are excluded: ephemeral (gone after 24h), and their insights
-- use a different metric shape (story_*) than feed/reel/carousel posts.

with media as (
    select *
    from {{ source('instagram_business', 'media_history') }}
    where coalesce(media_product_type, '') != 'STORY'
),

insights as (
    select *
    from {{ source('instagram_business', 'media_insights') }}
)

select
    m.created_time                                                         as created_timestamp,
    case when m.media_product_type = 'REELS' then 'REEL' else m.media_type end
                                                                            as media_type,
    m.username                                                             as account_name,
    m.user_id,
    m.id                                                                   as post_id,
    m.caption                                                              as post_caption,
    m.permalink                                                            as post_url,

    i.like_count,
    i.comment_count,
    i.carousel_album_impressions,
    i.story_impressions,
    i.video_photo_impressions,
    i.video_photo_views,
    i.reel_views,
    i.carousel_album_views                                                 as carousel_album_video_views,

    cast(null as string)                                                   as source_relation

from media m
left join insights i on i.id = m.id
