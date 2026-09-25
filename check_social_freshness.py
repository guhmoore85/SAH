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
    "fivetran_facebook.facebook_pages__posts_report (raw source)": f"""
        SELECT MAX(date_day) AS max_date, COUNT(*) AS row_count
        FROM `{PROJECT}.social_media_reporting_facebook_pages.facebook_pages__posts_report`
    """,
    "fivetran_instagram.instagram_business__posts (raw source)": f"""
        SELECT MAX(created_timestamp) AS max_date, COUNT(*) AS row_count
        FROM `{PROJECT}.social_media_reporting_instagram_business.instagram_business__posts`
    """,
    "social_media.combined_metrics_full, by platform (dbt-rebuilt output)": f"""
        SELECT platform, MAX(date) AS max_date, COUNT(*) AS row_count
        FROM `{PROJECT}.social_media.combined_metrics_full`
        GROUP BY platform
        ORDER BY platform
    """,
}


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


if __name__ == "__main__":
    main()
