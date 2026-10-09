#!/usr/bin/env python3
"""
Follower Count Source Finder

Diagnostic script: follower counts never reach the dashboards -- every
dbt model sets new_followers / total_followers to null, because they are
account-level metrics and the pipeline only reads post-level tables. The
Fivetran connectors may still sync them in account/page tables. This finds
every BigQuery column whose name looks like a follower count and prints its
latest values, so a staging model can be built on the right table.

Read-only.

Usage:
    python check_follower_sources.py

Reads:
    GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_FILE
    BQ_PROJECT (default: stopaapihate-472516)
"""

import json
import os
import re

from google.cloud import bigquery
from google.oauth2 import service_account
from dotenv import load_dotenv

load_dotenv()

PROJECT = os.getenv("BQ_PROJECT", "stopaapihate-472516")

# Follower-ish column names used by the Fivetran Facebook Pages, Instagram
# Business, TikTok and LinkedIn connectors.
COLUMN_PATTERN = r"(follower|fans|fan_count|subscriber)"
# Columns that date a row, in order of preference.
DATE_COLUMNS = ["date", "date_day", "_fivetran_synced", "updated_at", "updated_time"]


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


def main() -> None:
    client = get_client()
    datasets = [d.dataset_id for d in client.list_datasets()]

    found: dict[tuple[str, str], list[str]] = {}
    all_columns: dict[tuple[str, str], set[str]] = {}
    for ds in datasets:
        try:
            rows = client.query(f"""
                SELECT table_name, column_name, data_type
                FROM `{PROJECT}.{ds}`.INFORMATION_SCHEMA.COLUMNS
            """).result()
        except Exception as e:
            print(f"  (skipping {ds}: {e})")
            continue
        for r in rows:
            all_columns.setdefault((ds, r.table_name), set()).add(r.column_name)
            if (r.data_type.startswith(("INT", "FLOAT", "NUMERIC", "BIGNUMERIC"))
                    and re.search(COLUMN_PATTERN, r.column_name.lower())):
                found.setdefault((ds, r.table_name), []).append(r.column_name)

    print("=" * 70)
    print("Numeric follower-like columns by table")
    print("=" * 70)
    if not found:
        print("  none found in any dataset")
    for (ds, tbl), cols in sorted(found.items()):
        print(f"  {ds}.{tbl}: {', '.join(cols)}")
    print()

    for (ds, tbl), cols in sorted(found.items()):
        date_col = next((c for c in DATE_COLUMNS if c in all_columns[(ds, tbl)]), None)
        print("=" * 70)
        print(f"{ds}.{tbl}  (dated by {date_col or 'nothing'})")
        print("=" * 70)
        select = ", ".join(f"`{c}`" for c in cols)
        order = f"ORDER BY `{date_col}` DESC" if date_col else ""
        date_sel = f"`{date_col}`, " if date_col else ""
        try:
            info = list(client.query(f"""
                SELECT COUNT(*) AS n
                       {f', MIN(`{date_col}`) AS first_row, MAX(`{date_col}`) AS last_row' if date_col else ''}
                FROM `{PROJECT}.{ds}.{tbl}`
            """).result())[0]
            print(f"  rows={info.n}" + (f" first={info.first_row} last={info.last_row}" if date_col else ""))
            for r in client.query(f"""
                SELECT {date_sel}{select} FROM `{PROJECT}.{ds}.{tbl}` {order} LIMIT 5
            """).result():
                print(f"  {dict(r)}")
        except Exception as e:
            print(f"  ERROR: {e}")
        print()


if __name__ == "__main__":
    main()
