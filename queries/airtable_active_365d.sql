-- queries/airtable_active_365d.sql
--
-- GA4 active-users rollup for the past 365 days, broken out by four
-- dimension types: Page, Location, Device, Campaign.
--
-- FIX: engagement_time_msec is stored in event_params as int_value.
--      The prior query read double_value, which is always NULL for this
--      field → SUM returned NULL → visualization computed NULL/sessions
--      = NaN/Infinity, causing an infinite re-render loop.
--      Reading int_value and dividing by 1,000 (ms → seconds) yields
--      the live number expected by the dashboard.
--
-- Destination: Google Sheets "Airtable_Active_365" tab
--              → synced to Airtable via sync_ga4_airtable.py
--
-- Usage: replace REPLACE_WITH_GA4_PROPERTY_ID with your numeric GA4
--        property ID (found in GA4 Admin → Property Settings).

DECLARE cutoff DATE DEFAULT DATE_SUB(CURRENT_DATE(), INTERVAL 365 DAY);

-- ---------------------------------------------------------------------------
-- Base event stream (last 365 days)
-- ---------------------------------------------------------------------------
WITH events AS (
  SELECT
    PARSE_DATE('%Y%m%d', event_date)                                      AS date,
    user_pseudo_id,
    event_name,

    -- Session ID
    (SELECT ep.value.int_value
       FROM UNNEST(event_params) ep
      WHERE ep.key = 'ga_session_id')                                     AS session_id,

    -- Engaged session flag ('1' = engaged, NULL = not engaged)
    (SELECT ep.value.string_value
       FROM UNNEST(event_params) ep
      WHERE ep.key = 'session_engaged')                                   AS session_engaged,

    -- THE FIX: engagement_time_msec → int_value (never double_value).
    -- COALESCE to 0 so SUM never produces NULL from non-engagement events.
    COALESCE(
      (SELECT ep.value.int_value
         FROM UNNEST(event_params) ep
        WHERE ep.key = 'engagement_time_msec'),
      0)                                                                   AS engagement_time_msec,

    -- Page path (strip query string for cleaner grouping)
    REGEXP_REPLACE(
      COALESCE(
        (SELECT ep.value.string_value
           FROM UNNEST(event_params) ep
          WHERE ep.key = 'page_location'),
        ''),
      r'\?.*$', '')                                                        AS page_location,

    geo.country                                                            AS country,
    device.category                                                        AS device_category,

    -- Campaign = source / medium
    CONCAT(
      COALESCE(traffic_source.source, '(direct)'),
      ' / ',
      COALESCE(traffic_source.medium, '(none)'))                          AS campaign

  FROM `stopaapihate-472516.analytics_REPLACE_WITH_GA4_PROPERTY_ID.events_*`
  WHERE _TABLE_SUFFIX >= FORMAT_DATE('%Y%m%d', cutoff)
),

-- ---------------------------------------------------------------------------
-- New-user flag: GA4 fires first_visit only on a user's very first session
-- ---------------------------------------------------------------------------
first_visits AS (
  SELECT DISTINCT date, user_pseudo_id
  FROM   events
  WHERE  event_name = 'first_visit'
),

-- ---------------------------------------------------------------------------
-- Page dimension  (user_engagement_duration populated here only)
-- ---------------------------------------------------------------------------
page_metrics AS (
  SELECT
    e.date,
    'Page'                                                                AS dimension_type,
    NULLIF(e.page_location, '')                                           AS dimension_value,
    COUNT(DISTINCT e.user_pseudo_id)                                      AS users,
    COUNT(DISTINCT fv.user_pseudo_id)                                     AS new_users,
    COUNT(DISTINCT CONCAT(e.user_pseudo_id, '-',
                          CAST(e.session_id AS STRING)))                  AS sessions,
    ROUND(
      SAFE_DIVIDE(
        COUNTIF(e.session_engaged = '1'),
        COUNT(DISTINCT CONCAT(e.user_pseudo_id, '-',
                              CAST(e.session_id AS STRING)))),
      8)                                                                   AS engagement_rate,
    COUNTIF(e.event_name IN ('purchase', 'generate_lead',
                             'form_submit', 'sign_up',
                             'begin_checkout'))                           AS key_events,

    -- ms → seconds; this was the broken field (was NULL, now a live integer)
    CAST(SUM(e.engagement_time_msec) / 1000 AS INT64)                    AS user_engagement_duration

  FROM events e
  LEFT JOIN first_visits fv
         ON fv.date = e.date AND fv.user_pseudo_id = e.user_pseudo_id
  WHERE e.page_location != ''
  GROUP BY 1, 2, 3
),

-- ---------------------------------------------------------------------------
-- Location dimension  (user_engagement_duration intentionally NULL)
-- ---------------------------------------------------------------------------
location_metrics AS (
  SELECT
    e.date,
    'Location'                                                            AS dimension_type,
    COALESCE(e.country, '(not set)')                                      AS dimension_value,
    COUNT(DISTINCT e.user_pseudo_id)                                      AS users,
    COUNT(DISTINCT fv.user_pseudo_id)                                     AS new_users,
    COUNT(DISTINCT CONCAT(e.user_pseudo_id, '-',
                          CAST(e.session_id AS STRING)))                  AS sessions,
    ROUND(
      SAFE_DIVIDE(
        COUNTIF(e.session_engaged = '1'),
        COUNT(DISTINCT CONCAT(e.user_pseudo_id, '-',
                              CAST(e.session_id AS STRING)))),
      8)                                                                   AS engagement_rate,
    COUNTIF(e.event_name IN ('purchase', 'generate_lead',
                             'form_submit', 'sign_up',
                             'begin_checkout'))                           AS key_events,
    CAST(NULL AS INT64)                                                   AS user_engagement_duration
  FROM events e
  LEFT JOIN first_visits fv
         ON fv.date = e.date AND fv.user_pseudo_id = e.user_pseudo_id
  GROUP BY 1, 2, 3
),

-- ---------------------------------------------------------------------------
-- Device dimension  (user_engagement_duration intentionally NULL)
-- ---------------------------------------------------------------------------
device_metrics AS (
  SELECT
    e.date,
    'Device'                                                              AS dimension_type,
    COALESCE(e.device_category, '(not set)')                             AS dimension_value,
    COUNT(DISTINCT e.user_pseudo_id)                                      AS users,
    COUNT(DISTINCT fv.user_pseudo_id)                                     AS new_users,
    COUNT(DISTINCT CONCAT(e.user_pseudo_id, '-',
                          CAST(e.session_id AS STRING)))                  AS sessions,
    ROUND(
      SAFE_DIVIDE(
        COUNTIF(e.session_engaged = '1'),
        COUNT(DISTINCT CONCAT(e.user_pseudo_id, '-',
                              CAST(e.session_id AS STRING)))),
      8)                                                                   AS engagement_rate,
    COUNTIF(e.event_name IN ('purchase', 'generate_lead',
                             'form_submit', 'sign_up',
                             'begin_checkout'))                           AS key_events,
    CAST(NULL AS INT64)                                                   AS user_engagement_duration
  FROM events e
  LEFT JOIN first_visits fv
         ON fv.date = e.date AND fv.user_pseudo_id = e.user_pseudo_id
  GROUP BY 1, 2, 3
),

-- ---------------------------------------------------------------------------
-- Campaign dimension  (user_engagement_duration intentionally NULL)
-- ---------------------------------------------------------------------------
campaign_metrics AS (
  SELECT
    e.date,
    'Campaign'                                                            AS dimension_type,
    e.campaign                                                            AS dimension_value,
    COUNT(DISTINCT e.user_pseudo_id)                                      AS users,
    COUNT(DISTINCT fv.user_pseudo_id)                                     AS new_users,
    COUNT(DISTINCT CONCAT(e.user_pseudo_id, '-',
                          CAST(e.session_id AS STRING)))                  AS sessions,
    ROUND(
      SAFE_DIVIDE(
        COUNTIF(e.session_engaged = '1'),
        COUNT(DISTINCT CONCAT(e.user_pseudo_id, '-',
                              CAST(e.session_id AS STRING)))),
      8)                                                                   AS engagement_rate,
    COUNTIF(e.event_name IN ('purchase', 'generate_lead',
                             'form_submit', 'sign_up',
                             'begin_checkout'))                           AS key_events,
    CAST(NULL AS INT64)                                                   AS user_engagement_duration
  FROM events e
  LEFT JOIN first_visits fv
         ON fv.date = e.date AND fv.user_pseudo_id = e.user_pseudo_id
  GROUP BY 1, 2, 3
),

-- ---------------------------------------------------------------------------
-- Union all dimensions
-- ---------------------------------------------------------------------------
combined AS (
  SELECT * FROM page_metrics
  UNION ALL
  SELECT * FROM location_metrics
  UNION ALL
  SELECT * FROM device_metrics
  UNION ALL
  SELECT * FROM campaign_metrics
)

-- ---------------------------------------------------------------------------
-- Final output — column names match Airtable field definitions exactly
-- ---------------------------------------------------------------------------
SELECT
  FORMAT_DATE('%Y-%m-%d', date)         AS date,
  dimension_type,
  dimension_value,
  users,
  new_users,
  sessions,
  engagement_rate,
  key_events,
  user_engagement_duration,
  EXTRACT(YEAR  FROM date)              AS year,
  EXTRACT(MONTH FROM date)              AS month,
  EXTRACT(WEEK  FROM date)              AS week_of_year,
  FORMAT_DATE('%B', date)               AS month_name,
  FORMAT_DATE('%A', date)               AS day_of_week,
  DATE_DIFF(CURRENT_DATE(), date, DAY)  AS days_ago
FROM combined
WHERE dimension_value IS NOT NULL
ORDER BY date DESC, dimension_type, dimension_value
