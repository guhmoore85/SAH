-- Mart model: Daily subscription status totals across all three list reports
-- Reads from the summary-format rows written by sync_everyaction_gmail.py
-- (summary_only=True mode, active since 2026-03-19).
--
-- Each source table stores 1 row per day with:
--   record_count               INT64  - total contacts in the list
--   subscription_status_counts STRING - JSON {"Subscribed": N, "Unsubscribed": N}
--
-- Filters: record_count > 1 excludes both pre-3/19 individual rows (NULL)
-- and the broken 3/19-3/24 transition rows where count was recorded as 1.
-- Dedup: keeps the latest snapshot per date in case multiple runs landed same day.

with daily_full_list as (
    select
        _email_date,
        _import_timestamp,
        'daily_full_list'                                                               as source_table,
        record_count                                                                    as total_records,
        cast(json_extract_scalar(subscription_status_counts, '$.Subscribed')   as int64) as subscribed,
        cast(json_extract_scalar(subscription_status_counts, '$.Unsubscribed') as int64) as unsubscribed
    from {{ source('everyaction_reports', 'daily_full_list') }}
    where record_count > 1
),

daily_sah_365 as (
    select
        _email_date,
        _import_timestamp,
        'daily_sah_365'                                                                 as source_table,
        record_count                                                                    as total_records,
        cast(json_extract_scalar(subscription_status_counts, '$.Subscribed')   as int64) as subscribed,
        cast(json_extract_scalar(subscription_status_counts, '$.Unsubscribed') as int64) as unsubscribed
    from {{ source('everyaction_reports', 'daily_sah_365') }}
    where record_count > 1
),

sah_donors as (
    select
        _email_date,
        _import_timestamp,
        'sah_donors'                                                                    as source_table,
        record_count                                                                    as total_records,
        cast(json_extract_scalar(subscription_status_counts, '$.Subscribed')   as int64) as subscribed,
        cast(json_extract_scalar(subscription_status_counts, '$.Unsubscribed') as int64) as unsubscribed
    from {{ source('everyaction_reports', 'sah_donors') }}
    where record_count > 1
),

combined as (
    select * from daily_full_list
    union all
    select * from daily_sah_365
    union all
    select * from sah_donors
),

deduped as (
    select *,
        row_number() over (
            partition by _email_date, source_table
            order by _import_timestamp desc
        ) as rn
    from combined
)

select
    date(timestamp(_email_date))                                                                     as _email_date,
    source_table,
    total_records,
    coalesce(subscribed, 0)                                                                          as subscribed,
    coalesce(unsubscribed, 0)                                                                        as unsubscribed,
    total_records - coalesce(subscribed, 0) - coalesce(unsubscribed, 0)                              as status_unknown,
    round(safe_divide(coalesce(subscribed, 0),   total_records) * 100, 2)                            as pct_subscribed,
    round(safe_divide(coalesce(unsubscribed, 0), total_records) * 100, 2)                            as pct_unsubscribed
from deduped
where rn = 1
order by _email_date desc, source_table
