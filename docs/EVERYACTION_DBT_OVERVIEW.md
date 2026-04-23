# EveryAction Data Pipeline: dbt Overview

## What This Does

EveryAction (our CRM/email platform) emails us weekly CSV reports. A Python script (`sync_everyaction_gmail.py`) picks those up from Gmail and loads them raw into BigQuery. From there, dbt transforms the raw tables into a clean combined view that feeds a Google Sheet, which syncs nightly to Airtable.

```
EveryAction → Gmail → BigQuery (raw) → dbt → BigQuery (views) → Google Sheets → Airtable
```

---

## Why dbt?

Before dbt, we were running ad-hoc `CREATE OR REPLACE TABLE` SQL queries manually in BigQuery whenever we needed to reshape the data. This broke silently in March 2026 when EveryAction changed its export format — the daily subscription count tables started storing one summary row per day (with a JSON breakdown) instead of one row per contact. The old SQL was doing `COUNT(*)` expecting individual rows, so it returned "1" for every day after the format change.

dbt gives us:
- **Version-controlled SQL** — all transformations live in Git, not someone's clipboard
- **Automated runs** — GitHub Actions runs `dbt build` at 5:45 AM UTC daily, before the Google Sheets refresh
- **A single place to add fields** — when EveryAction adds a column, you update one staging model and everything downstream picks it up

---

## Project Structure

```
dbt_everyaction/
├── models/
│   ├── staging/
│   │   ├── sources.yml              # declares the 5 raw BigQuery tables
│   │   ├── stg_email_campaigns.sql  # cleans email_comparison → standard schema
│   │   └── stg_forms.sql            # cleans forms_report → standard schema
│   └── marts/
│       ├── everyaction_combined.sql # UNION ALL of both staging models
│       ├── daily_totals.sql         # subscription counts, parses JSON breakdown
│       └── marts.yml                # column docs + tests
├── profiles.yml                     # BigQuery connection (dev + prod targets)
└── dbt_project.yml                  # project config
```

All models are materialized as **views** in the `everyaction_reports` dataset in BigQuery project `stopaapihate-472516`.

---

## Key Models

**`everyaction_combined`** — the main output. 26-column unified view of all email campaigns and forms. This is what the Google Sheets BigQuery connector queries, and what ultimately syncs to Airtable.

**`daily_totals`** — subscription status counts (Subscribed / Unsubscribed / Unknown) across three lists: `daily_full_list`, `daily_sah_365`, and `sah_donors`. Parses the JSON `subscription_status_counts` column introduced in the March 2026 format change.

---

## Running Locally

```bash
cd ~/Downloads/SAH/dbt_everyaction

# first time only: create the service account key file
mkdir -p ~/.dbt
# paste the JSON from GitHub Secrets → GOOGLE_SERVICE_ACCOUNT_JSON
nano ~/.dbt/stopaapihate-service-account.json

dbt debug --profiles-dir .   # verify connection
dbt run --profiles-dir .     # rebuild all views
dbt test --profiles-dir .    # run data quality tests
```

---

## CI/CD

`.github/workflows/dbt-everyaction.yml` runs `dbt build` daily at **5:45 AM UTC** using a service account JSON stored in GitHub Secrets (`DBT_BIGQUERY_KEYFILE_JSON`). The Google Sheets refresh runs at 6 AM, so the views are always rebuilt before the sheet reads them.

To force a full refresh (e.g. after a schema change): go to GitHub Actions → `dbt-everyaction` → Run workflow → check **full-refresh**.

---

## Adding a New Field

1. Find the column in the raw BigQuery table (`everyaction_reports.email_comparison` or `everyaction_reports.forms_report`)
2. Add it to the relevant staging model (`stg_email_campaigns.sql` or `stg_forms.sql`)
3. Add `cast(null as <type>) as <column>` for the same column in the *other* staging model
4. Add the column to the `select` list in `everyaction_combined.sql`
5. Document it in `marts.yml`
6. `git push` — the nightly CI run will deploy it, or trigger manually via GitHub Actions
