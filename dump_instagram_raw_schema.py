#!/usr/bin/env python3
"""
Dump columns + a sample row for the raw Instagram Fivetran tables.

We've confirmed instagram_business.media_history and friends are current
(synced today), but Fivetran's own transformation into
instagram_business__posts is stuck at April. This dumps what's actually
available in the raw tables so we can build our own dbt staging model
directly off them, bypassing the stalled Fivetran transformation.

Usage:
    python dump_instagram_raw_schema.py
"""

import json
import os

from google.cloud import bigquery
from google.oauth2 import service_account
from dotenv import load_dotenv

load_dotenv()

PROJECT = os.getenv("BQ_PROJECT", "stopaapihate-472516")
DATASET = "instagram_business"
TABLES = ["media_history", "media_insights", "user_history", "user_insights", "user_lifetime_insights"]


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


def main() -> None:
    client = get_client()

    for table in TABLES:
        full_id = f"{PROJECT}.{DATASET}.{table}"
        print("=" * 70)
        print(f"{DATASET}.{table}")
        print("=" * 70)

        try:
            cols = list(
                client.query(f"""
                    SELECT column_name, data_type
                    FROM `{PROJECT}.{DATASET}.INFORMATION_SCHEMA.COLUMNS`
                    WHERE table_name = '{table}'
                    ORDER BY ordinal_position
                """).result()
            )
        except Exception as e:
            print(f"  ERROR listing columns: {e}")
            print()
            continue

        if not cols:
            print(f"  Table not found: {full_id}")
            print()
            continue

        print("  Columns:")
        for c in cols:
            print(f"    {c.column_name}: {c.data_type}")

        try:
            sample = list(client.query(f"SELECT * FROM `{full_id}` ORDER BY _fivetran_synced DESC LIMIT 1").result())
            print("\n  Most recently synced row:")
            for row in sample:
                print(f"    {dict(row)}")
        except Exception as e:
            print(f"  ERROR fetching sample row: {e}")
        print()


if __name__ == "__main__":
    main()
