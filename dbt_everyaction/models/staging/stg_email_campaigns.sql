-- Staging model: Email campaigns from the weekly Email Comparison Report
-- Source columns come directly from EveryAction CSV exports.
-- Add or rename columns here when EveryAction exports change.

with source as (
    select * from {{ source('everyaction_reports', 'email_comparison') }}
),

renamed as (
    select
        -- Identity
        'Email'                                              as type,
        cast(email_name as string)                           as name,

        -- Dates
        cast(first_sent_date as date)                        as first_date,
        cast(last_sent_date as date)                         as last_date,
        date_diff(
            cast(last_sent_date as date),
            cast(first_sent_date as date),
            day
        )                                                    as days_active,
        extract(year from cast(first_sent_date as date))     as year,
        extract(month from cast(first_sent_date as date))    as month,
        format_date('%B', cast(first_sent_date as date))     as month_name,

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

    from source
)

select * from renamed
