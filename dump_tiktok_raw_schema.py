#!/usr/bin/env python3
"""
Discover and dump the raw TikTok (tiktok_organic_app) Fivetran-synced tables.

TikTok isn't referenced anywhere in dbt_cross_channel today, but the
tiktok_organic_app connector is Active in Fivetran. This finds whatever
dataset(s) it synced to and dumps columns + a sample row for each table,
so we can build a stg_tiktok.sql staging model the same way we did for
Instagram.

Usage:
    python dump_tiktok_raw_schema.py
"""

import json
import os

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


def main() -> None:
    client = get_client()

    print("=" * 70)
    print("Datasets matching '%tiktok%'")
    print("=" * 70)
    datasets = list(
        client.query(f"""
            SELECT schema_name
            FROM `{PROJECT}`.INFORMATION_SCHEMA.SCHEMATA
            WHERE LOWER(schema_name) LIKE '%tiktok%'
            ORDER BY schema_name
        """).result()
    )
    for d in datasets:
        print(f"  {d.schema_name}")
    print()

    if not datasets:
        print("No matching datasets found via INFORMATION_SCHEMA.SCHEMATA.")
        return

    for d in datasets:
        dataset = d.schema_name
        print("=" * 70)
        print(f"Tables in {dataset}")
        print("=" * 70)

        tables = list(
            client.query(f"""
                SELECT table_name
                FROM `{PROJECT}.{dataset}`.INFORMATION_SCHEMA.TABLES
                ORDER BY table_name
            """).result()
        )
        for t in tables:
            print(f"  {t.table_name}")
        print()

        for t in tables:
            table = t.table_name
            full_id = f"{PROJECT}.{dataset}.{table}"
            print("-" * 70)
            print(f"{dataset}.{table}")
            print("-" * 70)

            cols = list(
                client.query(f"""
                    SELECT column_name, data_type
                    FROM `{PROJECT}.{dataset}`.INFORMATION_SCHEMA.COLUMNS
                    WHERE table_name = '{table}'
                    ORDER BY ordinal_position
                """).result()
            )
            print("  Columns:")
            for c in cols:
                print(f"    {c.column_name}: {c.data_type}")

            try:
                sample = list(client.query(f"SELECT * FROM `{full_id}` LIMIT 1").result())
                print("\n  Sample row:")
                for row in sample:
                    print(f"    {dict(row)}")
            except Exception as e:
                print(f"  ERROR fetching sample row: {e}")
            print()


if __name__ == "__main__":
    main()
