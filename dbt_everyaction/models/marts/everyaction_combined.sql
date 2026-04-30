-- Mart model: Combined EveryAction data for Google Sheets + Airtable
-- This is the single view that powers the downstream sync pipeline.
--
-- To add new fields:
--   1. Add the column to the relevant staging model (stg_email_campaigns or stg_forms)
--   2. Add it to the select list below
--   3. Run: dbt run --select everyaction_combined
--   4. The Google Sheets connected sheet and Airtable sync will pick it up automatically

with emails as (
    select * from {{ ref('stg_email_campaigns') }}
),

forms as (
    select * from {{ ref('stg_forms') }}
),

combined as (
    select
        type,
        name,
        first_date,
        last_date,
        days_active,
        year,
        month,
        month_name,
        conversion_rate,
        engagement_rate,
        total_contributions,
        amount_raised,
        avg_contribution_amount,
        recipients,
        unique_open_rate,
        unique_click_rate,
        bounce_rate,
        unsubscribe_rate,
        submissions,
        views,
        form_type,
        form_status,
        recurring_commitments,
        new_contacts,
        recipient_list,
        excluded_list
    from emails

    union all

    select
        type,
        name,
        first_date,
        last_date,
        days_active,
        year,
        month,
        month_name,
        conversion_rate,
        engagement_rate,
        total_contributions,
        amount_raised,
        avg_contribution_amount,
        recipients,
        unique_open_rate,
        unique_click_rate,
        bounce_rate,
        unsubscribe_rate,
        submissions,
        views,
        form_type,
        form_status,
        recurring_commitments,
        new_contacts,
        recipient_list,
        excluded_list
    from forms
)

select *
from combined
order by last_date desc
