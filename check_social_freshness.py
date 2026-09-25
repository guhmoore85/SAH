#!/usr/bin/env python3
"""
Social Media Data Freshness Checker

Diagnostic script: checks how current the Facebook/Instagram data actually
is, at each layer of the pipeline:
    Fivetran-managed source tables -> dbt-rebuilt social_media.combined_metrics_full

dbt_cross_channel's `combined_metrics_full` model fully rebuilds itself
from the Fivetran source tables on every run (materialized='table'), so a
green "dbt build" only proves the SQL ran without error -- it says nothing
about whether Fivetran itself is still syncing new rows. This script
queries the actual MAX(date) and row counts at each layer so we can see
where staleness is coming from.

Usage:
    python check_social_freshness.py

Reads the same env vars as the dbt profile (see dbt_cross_channel/profiles.yml):
    GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_FILE
"""

import json
import os
import sys

from google.cloud import bigquery
from google.oauth2 import service_account
from dotenv import load_dotenv

load_dotenv()

PROJECT = os.getenv("BQ_PROJECT", "stopaapihate-472516")


def get_client() -> bigquery.Client:
    json_str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if json_str:
        info = json.loads(json_str)
        creds = service_account.Credentials.from_service_account_info(info)
        return bigquery.Client(project=PROJECT, credentials=creds)

    file_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    if file_path and os.path.isfile(file_path):
        creds = service_account.Credentials.from_service_account_file(file_path)
        return bigquery.Client(project=PROJECT, credentials=creds)

    raise RuntimeError("Set GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_FILE")


QUERIES = {
    "OLD: social_media_reporting_facebook_pages.facebook_pages__posts_report (sources.yml today)": f"""
        SELECT MAX(date_day) AS max_date, COUNT(*) AS row_count
        FROM `{PROJECT}.social_media_reporting_facebook_pages.facebook_pages__posts_report`
    """,
    "OLD: social_media_reporting_instagram_business.instagram_business__posts (sources.yml today)": f"""
        SELECT MAX(created_timestamp) AS max_date, COUNT(*) AS row_count
        FROM `{PROJECT}.social_media_reporting_instagram_business.instagram_business__posts`
    """,
    "NEW: facebook_pages_facebook_pages.facebook_pages__posts_report (from the 2026-09-25 dbt run)": f"""
        SELECT MAX(date_day) AS max_date, COUNT(*) AS row_count
        FROM `{PROJECT}.facebook_pages_facebook_pages.facebook_pages__posts_report`
    """,
    "NEW: social_media_reporting_staging.instagram_business__posts (from the 2026-09-25 dbt run)": f"""
        SELECT MAX(created_timestamp) AS max_date, COUNT(*) AS row_count
        FROM `{PROJECT}.social_media_reporting_staging.instagram_business__posts`
    """,
    "social_media.combined_metrics_full, by platform (dbt-rebuilt output)": f"""
        SELECT platform, MAX(date) AS max_date, COUNT(*) AS row_count
        FROM `{PROJECT}.social_media.combined_metrics_full`
        GROUP BY platform
        ORDER BY platform
    """,
}

# Raw, pre-transformation Fivetran-synced tables -- unprefixed schema matching
# the connector name (confirmed via instagram_business.user_lifetime_insights,
# which is current as of today). Checks both whatever business-date column
# exists and Fivetran's own _fivetran_synced column, since a table can have
# _fivetran_synced advancing daily while the business date it's syncing stays
# frozen (API returning stale/limited data) -- those are different root causes.
RAW_TABLES = [
    ("instagram_business", "media_history"),
    ("facebook_pages", "post_history"),
]


def check_raw_table(client: bigquery.Client, dataset: str, table: str) -> None:
    full_id = f"{PROJECT}.{dataset}.{table}"
    print("=" * 70)
    print(f"RAW: {dataset}.{table}")
    print("=" * 70)

    try:
        cols = list(
            client.query(f"""
                SELECT column_name, data_type
                FROM `{PROJECT}.{dataset}.INFORMATION_SCHEMA.COLUMNS`
                WHERE table_name = '{table}'
            """).result()
        )
    except Exception as e:
        print(f"  ERROR listing columns: {e}")
        print()
        return

    if not cols:
        print(f"  Table not found: {full_id}")
        print()
        return

    date_col = None
    for c in cols:
        name_lower = c.column_name.lower()
        if c.data_type in ("TIMESTAMP", "DATE", "DATETIME") and "_fivetran" not in name_lower:
            date_col = c.column_name
            break

    select_parts = ["COUNT(*) AS row_count", "MAX(_fivetran_synced) AS max_fivetran_synced"]
    if date_col:
        select_parts.insert(0, f"MAX({date_col}) AS max_{date_col}")
    else:
        print("  (no obvious business-date column found; showing _fivetran_synced only)")

    sql = f"SELECT {', '.join(select_parts)} FROM `{full_id}`"
    try:
        rows = list(client.query(sql).result())
        for row in rows:
            print(f"  {dict(row)}")
    except Exception as e:
        print(f"  ERROR: {e}")
    print()


def main() -> None:
    client = get_client()

    for label, sql in QUERIES.items():
        print("=" * 70)
        print(label)
        print("=" * 70)
        try:
            rows = list(client.query(sql).result())
            if not rows:
                print("  (no rows returned)")
            for row in rows:
                print(f"  {dict(row)}")
        except Exception as e:
            print(f"  ERROR: {e}")
        print()

    for dataset, table in RAW_TABLES:
        check_raw_table(client, dataset, table)


if __name__ == "__main__":
    main()
