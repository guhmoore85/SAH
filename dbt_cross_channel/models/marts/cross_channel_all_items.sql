-- Mart: Cross-channel combined table for the executive dashboard
-- One row per content item (post, page view, email, or form)
-- Replaces the manual CREATE OR REPLACE TABLE query in BigQuery.
--
-- To add a new data source:
--   1. Add it to sources.yml
--   2. Create a staging model following the column pattern below
--   3. Add a UNION ALL branch here
--
-- To add a new column:
--   1. Add it to the relevant staging model(s)
--   2. Add cast(null as <type>) as <column> to the other staging models
--   3. Add it to the select list in each branch below

select * from {{ ref('stg_social') }}

union all

select * from {{ ref('stg_ga4') }}

union all

select * from {{ ref('stg_email_campaigns') }}

union all

select * from {{ ref('stg_forms') }}
