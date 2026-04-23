-- Staging model: Forms/pages from the weekly Forms Report
-- Source columns come directly from EveryAction CSV exports.
-- Add or rename columns here when EveryAction exports change.

with source as (
    select * from {{ source('everyaction_reports', 'forms_report') }}
),

deduped as (
    select *,
        row_number() over (
            partition by form_name
            order by _import_timestamp desc
        ) as rn
    from source
),

renamed as (
    select
        -- Identity
        'Form'                                                    as type,
        cast(form_name as string)                                 as name,

        -- Dates (source may store as STRING in M/D/YY format)
        coalesce(
            safe.parse_date('%m/%d/%y', cast(first_submission_date as string)),
            safe.parse_date('%m/%d/%Y', cast(first_submission_date as string)),
            safe_cast(first_submission_date as date)
        )                                                         as first_date,
        coalesce(
            safe.parse_date('%m/%d/%y', cast(last_submission_date as string)),
            safe.parse_date('%m/%d/%Y', cast(last_submission_date as string)),
            safe_cast(last_submission_date as date)
        )                                                         as last_date,
        date_diff(
            coalesce(
                safe.parse_date('%m/%d/%y', cast(last_submission_date as string)),
                safe.parse_date('%m/%d/%Y', cast(last_submission_date as string)),
                safe_cast(last_submission_date as date)
            ),
            coalesce(
                safe.parse_date('%m/%d/%y', cast(first_submission_date as string)),
                safe.parse_date('%m/%d/%Y', cast(first_submission_date as string)),
                safe_cast(first_submission_date as date)
            ),
            day
        )                                                         as days_active,
        extract(year from coalesce(
            safe.parse_date('%m/%d/%y', cast(first_submission_date as string)),
            safe.parse_date('%m/%d/%Y', cast(first_submission_date as string)),
            safe_cast(first_submission_date as date)
        ))                                                        as year,
        extract(month from coalesce(
            safe.parse_date('%m/%d/%y', cast(first_submission_date as string)),
            safe.parse_date('%m/%d/%Y', cast(first_submission_date as string)),
            safe_cast(first_submission_date as date)
        ))                                                        as month,
        format_date('%B', coalesce(
            safe.parse_date('%m/%d/%y', cast(first_submission_date as string)),
            safe.parse_date('%m/%d/%Y', cast(first_submission_date as string)),
            safe_cast(first_submission_date as date)
        ))                                                        as month_name,

        -- Form metrics (conversion_rate pre-computed in source)
        cast(number_of_submissions as int64)                      as submissions,
        cast(number_of_views as int64)                            as views,
        cast(form_type as string)                                 as form_type,
        cast(form_status as string)                               as form_status,
        cast(conversion_rate as float64)                          as conversion_rate,

        -- Contribution metrics (pre-computed in source)
        cast(number_of_contributions as int64)                    as total_contributions,
        cast(total_contribution_amount as float64)                as amount_raised,
        cast(average_contribution_amount as float64)              as avg_contribution_amount,
        cast(number_of_recurring_commitments as int64)            as recurring_commitments,
        cast(number_of_new_contacts as int64)                     as new_contacts,

        -- Not applicable to forms
        cast(null as int64)                                       as recipients,
        cast(null as float64)                                     as unique_open_rate,
        cast(null as float64)                                     as unique_click_rate,
        cast(null as float64)                                     as bounce_rate,
        cast(null as float64)                                     as unsubscribe_rate,
        cast(null as string)                                      as recipient_list,
        cast(null as string)                                      as excluded_list,

        -- Metadata
        _import_timestamp,
        _source_filename

    from deduped
    where rn = 1
)

select * from renamed
