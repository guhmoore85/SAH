-- Staging model: Forms/pages from the weekly Forms Report
-- Source columns come directly from EveryAction CSV exports.
-- Add or rename columns here when EveryAction exports change.

with source as (
    select * from {{ source('everyaction_reports', 'forms_report') }}
),

renamed as (
    select
        -- Identity
        'Form'                                           as type,
        cast(form_name as string)                        as name,

        -- Dates
        cast(date_created as date)                       as first_date,
        cast(last_submission_date as date)                as last_date,
        date_diff(
            cast(last_submission_date as date),
            cast(date_created as date),
            day
        )                                                as days_active,
        extract(year from cast(date_created as date))    as year,
        extract(month from cast(date_created as date))   as month,
        format_date('%B', cast(date_created as date))    as month_name,

        -- Form metrics
        cast(submissions as int64)                       as submissions,
        cast(views as int64)                             as views,
        cast(form_type as string)                        as form_type,
        cast(form_status as string)                      as form_status,
        safe_divide(
            cast(submissions as int64),
            nullif(cast(views as int64), 0)
        )                                                as conversion_rate,

        -- Contribution metrics
        cast(total_contributions as int64)               as total_contributions,
        cast(amount_raised as float64)                   as amount_raised,
        safe_divide(
            cast(amount_raised as float64),
            nullif(cast(total_contributions as int64), 0)
        )                                                as avg_contribution_amount,
        cast(recurring_commitments as int64)             as recurring_commitments,
        cast(new_contacts as int64)                      as new_contacts,

        -- Not applicable to forms
        cast(null as int64)                              as recipients,
        cast(null as float64)                            as unique_open_rate,
        cast(null as float64)                            as unique_click_rate,
        cast(null as float64)                             as bounce_rate,
        cast(null as float64)                            as unsubscribe_rate,
        cast(null as string)                             as recipient_list,
        cast(null as string)                             as excluded_list,

        -- Metadata
        _import_timestamp,
        _source_filename

    from source
)

select * from renamed
