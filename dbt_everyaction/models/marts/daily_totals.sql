-- Mart model: Daily subscription status totals across all three list reports
-- Reads from the summary-format rows written by sync_everyaction_gmail.py
-- (summary_only=True mode, active since 2026-03-19).
--
-- Each source table stores 1 row per day with:
--   record_count               INT64  - total contacts in the list
--   subscription_status_counts STRING - JSON {"Subscribed": N, "Unsubscribed": N}
--
-- Pre-3/19 legacy rows (individual contacts) are excluded via WHERE record_count IS NOT NULL.

with daily_full_list as (
    select
        _email_date,
        'daily_full_list'                                                               as source_table,
        record_count                                                                    as total_records,
        cast(json_extract_scalar(subscription_status_counts, '$.Subscribed')   as int64) as subscribed,
        cast(json_extract_scalar(subscription_status_counts, '$.Unsubscribed') as int64) as unsubscribed
    from {{ source('everyaction_reports', 'daily_full_list') }}
    where record_count is not null
),

daily_sah_365 as (
    select
        _email_date,
        'daily_sah_365'                                                                 as source_table,
        record_count                                                                    as total_records,
        cast(json_extract_scalar(subscription_status_counts, '$.Subscribed')   as int64) as subscribed,
        cast(json_extract_scalar(subscription_status_counts, '$.Unsubscribed') as int64) as unsubscribed
    from {{ source('everyaction_reports', 'daily_sah_365') }}
    where record_count is not null
),

sah_donors as (
    select
        _email_date,
        'sah_donors'                                                                    as source_table,
        record_count                                                                    as total_records,
        cast(json_extract_scalar(subscription_status_counts, '$.Subscribed')   as int64) as subscribed,
        cast(json_extract_scalar(subscription_status_counts, '$.Unsubscribed') as int64) as unsubscribed
    from {{ source('everyaction_reports', 'sah_donors') }}
    where record_count is not null
),

combined as (
    select * from daily_full_list
    union all
    select * from daily_sah_365
    union all
    select * from sah_donors
)

select
    date(timestamp(_email_date)) as _email_date,
    source_table,
    total_records,
    coalesce(subscribed, 0)                                                                         as subscribed,
    coalesce(unsubscribed, 0)                                                                       as unsubscribed,
    total_records - coalesce(subscribed, 0) - coalesce(unsubscribed, 0)                             as status_unknown,
    round(safe_divide(coalesce(subscribed, 0),   total_records) * 100, 2)                           as pct_subscribed,
    round(safe_divide(coalesce(unsubscribed, 0), total_records) * 100, 2)                           as pct_unsubscribed
from combined
order by _email_date desc, source_table
