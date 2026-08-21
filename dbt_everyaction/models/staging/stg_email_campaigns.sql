-- Staging model: Email campaigns from the weekly Email Comparison Report
-- Source columns come directly from EveryAction CSV exports.
-- Add or rename columns here when EveryAction exports change.

with source as (
    select * from {{ source('everyaction_reports', 'email_comparison') }}
),

deduped as (
    select *,
        row_number() over (
            partition by email_name
            order by _import_timestamp desc
        ) as rn
    from source
),

renamed as (
    select
        -- Identity
        'Email'                                              as type,
        coalesce(cast(email_name as string), '(Unnamed Email)') as name,

        -- Dates (source may store as STRING in M/D/YY format)
        coalesce(
            safe.parse_date('%m/%d/%y', cast(first_sent_date as string)),
            safe.parse_date('%m/%d/%Y', cast(first_sent_date as string)),
            safe_cast(first_sent_date as date)
        )                                                    as first_date,
        coalesce(
            safe.parse_date('%m/%d/%y', cast(last_sent_date as string)),
            safe.parse_date('%m/%d/%Y', cast(last_sent_date as string)),
            safe_cast(last_sent_date as date)
        )                                                    as last_date,
        date_diff(
            coalesce(
                safe.parse_date('%m/%d/%y', cast(last_sent_date as string)),
                safe.parse_date('%m/%d/%Y', cast(last_sent_date as string)),
                safe_cast(last_sent_date as date)
            ),
            coalesce(
                safe.parse_date('%m/%d/%y', cast(first_sent_date as string)),
                safe.parse_date('%m/%d/%Y', cast(first_sent_date as string)),
                safe_cast(first_sent_date as date)
            ),
            day
        )                                                    as days_active,
        extract(year from coalesce(
            safe.parse_date('%m/%d/%y', cast(first_sent_date as string)),
            safe.parse_date('%m/%d/%Y', cast(first_sent_date as string)),
            safe_cast(first_sent_date as date)
        ))                                                   as year,
        extract(month from coalesce(
            safe.parse_date('%m/%d/%y', cast(first_sent_date as string)),
            safe.parse_date('%m/%d/%Y', cast(first_sent_date as string)),
            safe_cast(first_sent_date as date)
        ))                                                   as month,
        format_date('%B', coalesce(
            safe.parse_date('%m/%d/%y', cast(first_sent_date as string)),
            safe.parse_date('%m/%d/%Y', cast(first_sent_date as string)),
            safe_cast(first_sent_date as date)
        ))                                                   as month_name,

        -- Email metrics (rates are pre-computed in source)
        cast(recipients as int64)                            as recipients,
        cast(unique_open_rate as float64)                    as unique_open_rate,
        cast(unique_click_rate as float64)                   as unique_click_rate,
        cast(bounce_rate as float64)                         as bounce_rate,
        cast(unsubscribe_rate as float64)                    as unsubscribe_rate,

        -- Contribution metrics (pre-computed in source)
        cast(total_contributions as int64)                   as total_contributions,
        cast(amount_raised as float64)                       as amount_raised,
        cast(avg_contribution_amount as float64)             as avg_contribution_amount,
        cast(conversion_rate as float64)                     as conversion_rate,
        round(cast(unique_open_rate as float64) + cast(unique_click_rate as float64), 4) as engagement_rate,

        -- Not applicable to emails
        cast(null as int64)                                  as submissions,
        cast(null as int64)                                  as views,
        cast(null as string)                                 as form_type,
        cast(null as string)                                 as form_status,
        cast(null as int64)                                  as recurring_commitments,
        cast(null as int64)                                  as new_contacts,

        -- Lists
        cast(recipient_list as string)                       as recipient_list,
        cast(excluded_list as string)                        as excluded_list,

        -- Metadata
        _import_timestamp,
        _source_filename

    from deduped
    where rn = 1
)

select * from renamed
