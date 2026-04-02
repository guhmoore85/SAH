-- Staging model: Email campaigns from the weekly Email Comparison Report
-- Source columns come directly from EveryAction CSV exports.
-- Add or rename columns here when EveryAction exports change.

with source as (
    select * from {{ source('everyaction_reports', 'email_comparison') }}
),

renamed as (
    select
        -- Identity
        'Email'                                     as type,
        cast(campaign_name as string)                as name,

        -- Dates
        cast(date_sent as date)                      as first_date,
        cast(date_sent as date)                      as last_date,
        0                                            as days_active,
        extract(year from cast(date_sent as date))   as year,
        extract(month from cast(date_sent as date))  as month,
        format_date('%B', cast(date_sent as date))   as month_name,

        -- Email metrics
        cast(recipients as int64)                    as recipients,
        safe_divide(unique_opens, recipients)        as unique_open_rate,
        safe_divide(unique_clicks, recipients)       as unique_click_rate,
        safe_divide(bounces, recipients)             as bounce_rate,
        safe_divide(unsubscribes, recipients)        as unsubscribe_rate,

        -- Contribution metrics
        cast(total_contributions as int64)           as total_contributions,
        cast(amount_raised as float64)               as amount_raised,
        safe_divide(
            cast(amount_raised as float64),
            nullif(cast(total_contributions as int64), 0)
        )                                            as avg_contribution_amount,
        safe_divide(
            cast(total_contributions as int64),
            nullif(cast(recipients as int64), 0)
        )                                            as conversion_rate,

        -- Not applicable to emails
        cast(null as int64)                          as submissions,
        cast(null as int64)                          as views,
        cast(null as string)                         as form_type,
        cast(null as string)                         as form_status,
        cast(null as int64)                          as recurring_commitments,
        cast(null as int64)                          as new_contacts,

        -- Lists
        cast(recipient_list as string)               as recipient_list,
        cast(excluded_list as string)                as excluded_list,

        -- Metadata
        _import_timestamp,
        _source_filename

    from source
)

select * from renamed
