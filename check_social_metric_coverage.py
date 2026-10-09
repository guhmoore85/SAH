#!/usr/bin/env python3
"""
Social Reach / Views Coverage Check

Diagnostic script: social `reach` is never filled and `impressions` only
arrives for TikTok, because Meta retired post impressions in favour of
"views". This shows, for posts in the last 180 days:
    - how many posts per platform have each metric in combined_metrics_full
    - every reach/view/impression column in the raw Facebook and Instagram
      tables, and how many recent posts have a value in each
so reach can be rebuilt from columns that actually hold data.

Read-only.

Usage:
    python check_social_metric_coverage.py
"""

import json
import os

from google.cloud import bigquery
from google.oauth2 import service_account
from dotenv import load_dotenv

load_dotenv()

PROJECT = os.getenv("BQ_PROJECT", "stopaapihate-472516")
RAW_TABLES = [
    # (dataset, table, date column for the 180-day window)
    ("facebook_pages_facebook_pages", "facebook_pages__posts_report", "created_timestamp"),
    ("instagram_business", "media_insights", "_fivetran_synced"),
    ("instagram_business", "media_history", "created_time"),
]
METRIC_WORDS = ("reach", "view", "impression", "plays")


def get_client() -> bigquery.Client:
    json_str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if json_str:
        creds = service_account.Credentials.from_service_account_info(json.loads(json_str))
        return bigquery.Client(project=PROJECT, credentials=creds)
    file_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    if file_path and os.path.isfile(file_path):
        creds = service_account.Credentials.from_service_account_file(file_path)
        return bigquery.Client(project=PROJECT, credentials=creds)
    raise RuntimeError("Set GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_FILE")


def show(client: bigquery.Client, label: str, sql: str) -> None:
    print("=" * 70)
    print(label)
    print("=" * 70)
    try:
        for row in client.query(sql).result():
            print(f"  {dict(row)}")
    except Exception as e:
        print(f"  ERROR: {e}")
    print()


def main() -> None:
    client = get_client()

    show(client, "combined_metrics_full: posts with a value, last 180 days", f"""
        SELECT platform, COUNT(*) AS posts,
               COUNTIF(reach IS NOT NULL) AS reach,
               COUNTIF(impressions IS NOT NULL) AS impressions,
               COUNTIF(media_views IS NOT NULL) AS media_views,
               COUNTIF(video_views IS NOT NULL) AS video_views,
               COUNTIF(likes IS NOT NULL) AS likes,
               SUM(media_views) AS sum_media_views,
               SUM(video_views) AS sum_video_views,
               SUM(impressions) AS sum_impressions
        FROM `{PROJECT}.social_media.combined_metrics_full`
        WHERE date >= DATE_SUB(CURRENT_DATE(), INTERVAL 180 DAY)
        GROUP BY platform ORDER BY platform
    """)

    for ds, tbl, date_col in RAW_TABLES:
        cols = [r.column_name for r in client.query(f"""
            SELECT column_name FROM `{PROJECT}.{ds}`.INFORMATION_SCHEMA.COLUMNS
            WHERE table_name = '{tbl}' ORDER BY ordinal_position
        """).result()]
        metric_cols = [c for c in cols if any(w in c.lower() for w in METRIC_WORDS)]
        print(f"{ds}.{tbl} metric-like columns: {metric_cols}")
        if not metric_cols:
            print()
            continue
        counts = ", ".join(f"COUNTIF(`{c}` IS NOT NULL AND CAST(`{c}` AS STRING) != '0') AS `{c}`"
                           for c in metric_cols)
        show(client, f"{ds}.{tbl}: rows with a non-zero value, last 180 days", f"""
            SELECT COUNT(*) AS rows_total, {counts}
            FROM `{PROJECT}.{ds}.{tbl}`
            WHERE DATE(`{date_col}`) >= DATE_SUB(CURRENT_DATE(), INTERVAL 180 DAY)
        """)


if __name__ == "__main__":
    main()
