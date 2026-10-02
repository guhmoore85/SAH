#!/usr/bin/env python3
"""
GA4 Pipeline Inspector

Diagnostic script: GA4 totals in Airtable come out a multiple of what the
GA4 UI shows. google_analytics_4.airtable_active_365d has no _fivetran_*
columns, so it's built from something else (scheduled query, view, sheet).
This prints everything needed to find where the inflation happens:

  1. Every table/view in the google_analytics_4 dataset, with row counts,
     last-modified times, columns, and the DDL (view SQL) where there is one
  2. The SQL of recent jobs that wrote airtable_active_365d (i.e. the
     scheduled query that builds it, if that's how it's built)
  3. A few sample rows from each table
  4. Last 7 days of airtable_active_365d totals by dimension_type
  5. The same 7 days from the GA4 Airtable table, if Airtable creds are set

Usage:
    python check_ga4_pipeline.py

Env vars:
    GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_FILE
    BQ_PROJECT          - default: stopaapihate-472516
    GA4_DATASET         - default: google_analytics_4
    AIRTABLE_PAT, AIRTABLE_BASE_ID, AIRTABLE_TABLE_ID  (optional, step 5)
"""

import json
import os
import sys
from collections import defaultdict

from google.cloud import bigquery
from google.oauth2 import service_account
from dotenv import load_dotenv

load_dotenv()

PROJECT = os.getenv("BQ_PROJECT", "stopaapihate-472516")
DATASET = os.getenv("GA4_DATASET", "google_analytics_4")
TARGET = "airtable_active_365d"


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


def section(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def run(client: bigquery.Client, sql: str) -> list[dict]:
    try:
        return [dict(r) for r in client.query(sql).result()]
    except Exception as e:
        print(f"  QUERY FAILED: {e}")
        return []


def print_rows(rows: list[dict], width: int = 60) -> None:
    if not rows:
        print("  (no rows)")
        return
    for r in rows:
        print("  " + " | ".join(f"{k}={str(v)[:width]}" for k, v in r.items()))


def main() -> None:
    client = get_client()

    section("Datasets in project (look for other GA4-related ones)")
    for ds in client.list_datasets():
        print(f"  {ds.dataset_id}")

    dataset = client.get_dataset(f"{PROJECT}.{DATASET}")
    location = dataset.location
    print(f"\n  {DATASET} location: {location}")

    section(f"1. Tables in {DATASET}")
    tables = run(client, f"""
        SELECT t.table_name, t.table_type, t.creation_time,
               TIMESTAMP_MILLIS(m.last_modified_time) AS last_modified,
               m.row_count
        FROM `{PROJECT}.{DATASET}.INFORMATION_SCHEMA.TABLES` t
        LEFT JOIN `{PROJECT}.{DATASET}.__TABLES__` m
          ON m.table_id = t.table_name
        ORDER BY t.table_name
    """)
    print_rows(tables)

    columns = run(client, f"""
        SELECT table_name, STRING_AGG(column_name, ', ' ORDER BY ordinal_position) AS cols
        FROM `{PROJECT}.{DATASET}.INFORMATION_SCHEMA.COLUMNS`
        GROUP BY table_name ORDER BY table_name
    """)
    print("\n  Columns:")
    for r in columns:
        print(f"  {r['table_name']}: {r['cols']}")

    ddls = run(client, f"""
        SELECT table_name, table_type, ddl
        FROM `{PROJECT}.{DATASET}.INFORMATION_SCHEMA.TABLES`
        WHERE table_type != 'BASE TABLE' OR table_name = '{TARGET}'
    """)
    for r in ddls:
        print(f"\n  --- DDL for {r['table_name']} ({r['table_type']}) ---")
        print(r["ddl"])

    section(f"2. Recent jobs that wrote {TARGET} (the SQL that builds it)")
    jobs = run(client, f"""
        SELECT creation_time, user_email, statement_type, query
        FROM `{PROJECT}`.`region-{location.lower()}`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
        WHERE destination_table.dataset_id = '{DATASET}'
          AND destination_table.table_id = '{TARGET}'
          AND creation_time > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
        ORDER BY creation_time DESC
        LIMIT 3
    """)
    if not jobs:
        # Also catch DDL-style writes (CREATE OR REPLACE TABLE ... AS) whose
        # destination_table isn't set to the target
        jobs = run(client, f"""
            SELECT creation_time, user_email, statement_type, query
            FROM `{PROJECT}`.`region-{location.lower()}`.INFORMATION_SCHEMA.JOBS_BY_PROJECT
            WHERE REGEXP_CONTAINS(LOWER(query), r'{TARGET}')
              AND statement_type IN ('CREATE_TABLE_AS_SELECT', 'INSERT', 'MERGE',
                                     'CREATE_VIEW', 'DELETE', 'SCRIPT')
              AND creation_time > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
            ORDER BY creation_time DESC
            LIMIT 3
        """)
    for j in jobs:
        print(f"\n  --- {j['creation_time']} {j['statement_type']} by {j['user_email']} ---")
        print(j["query"])
    if not jobs:
        print("  (no writing jobs found in the last 30 days -- may be loaded from a sheet or another project)")

    section("3. Sample rows from each table")
    for t in tables:
        name = t["table_name"]
        print(f"\n  --- {name} ---")
        print_rows(run(client, f"SELECT * FROM `{PROJECT}.{DATASET}.{name}` LIMIT 3"), width=40)

    section(f"4. {TARGET}: last 7 days by dimension_type")
    print_rows(run(client, f"""
        SELECT date, dimension_type, COUNT(*) AS rows_, SUM(users) AS users,
               SUM(new_users) AS new_users, SUM(sessions) AS sessions,
               SUM(key_events) AS key_events
        FROM `{PROJECT}.{DATASET}.{TARGET}`
        WHERE date >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
        GROUP BY 1, 2 ORDER BY 1, 2
    """))
    print("\n  Device rows for the most recent full day:")
    print_rows(run(client, f"""
        SELECT date, dimension_value, users, new_users, sessions, engagement_rate, days_ago
        FROM `{PROJECT}.{DATASET}.{TARGET}`
        WHERE dimension_type = 'Device'
          AND date = (SELECT MAX(date) FROM `{PROJECT}.{DATASET}.{TARGET}`
                      WHERE date < CURRENT_DATE())
    """))

    section("6. Raw Fivetran reports vs airtable_active_365d (last 7 days)")
    print("\n  Properties synced:")
    print_rows(run(client, f"SELECT name, display_name, time_zone FROM `{PROJECT}.{DATASET}.properties`"))
    print("\n  Freshness of each Fivetran report (max date / last synced):")
    for t in tables:
        name = t["table_name"]
        if name.endswith("_report"):
            print_rows([{"table": name, **(run(client, f"""
                SELECT MAX(date) AS max_date, MAX(_fivetran_synced) AS last_synced,
                       COUNT(DISTINCT property) AS properties
                FROM `{PROJECT}.{DATASET}.{name}`
            """) or [{}])[0]}])
    raw_checks = {
        "tech_device_category_report": "device_category",
        "pages_path_report": "page_path",
        "demographic_city_report": "city",
        "demographic_region_report": "region",
        "demographic_country_report": "country",
        "traffic_acquisition_session_campaign_report": "session_campaign_name",
    }
    for name, dim in raw_checks.items():
        print(f"\n  --- {name}: per day (dup_keys = (date, property, {dim}) seen >1 time) ---")
        print_rows(run(client, f"""
            WITH t AS (
                SELECT *, COUNT(*) OVER (PARTITION BY date, property, {dim}) AS n
                FROM `{PROJECT}.{DATASET}.{name}`
                WHERE date >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)
            )
            SELECT date, COUNT(*) AS rows_, COUNT(DISTINCT {dim}) AS distinct_values,
                   COUNTIF(n > 1) AS dup_rows, SUM(total_users) AS total_users,
                   SUM(new_users) AS new_users
            FROM t GROUP BY date ORDER BY date
        """))
    print("\n  --- tech_device_category_report raw rows for the most recent full day ---")
    print_rows(run(client, f"""
        SELECT date, property, device_category, total_users, new_users,
               engaged_sessions, engagement_rate, key_events, _fivetran_synced
        FROM `{PROJECT}.{DATASET}.tech_device_category_report`
        WHERE date = DATE_SUB(CURRENT_DATE(), INTERVAL 2 DAY)
        ORDER BY device_category
    """))

    section("7. New social_media.ga4_active_365d vs old table vs raw Fivetran")
    new = f"{PROJECT}.social_media.ga4_active_365d"
    print_rows(run(client, f"""
        SELECT COUNT(*) AS rows_, MIN(date) AS min_date, MAX(date) AS max_date,
               COUNT(DISTINCT dimension_type) AS dim_types
        FROM `{new}`
    """))
    print("\n  Per day x dimension_type: new vs old (users / sessions)")
    print_rows(run(client, f"""
        WITH n AS (
            SELECT date, dimension_type, COUNT(*) AS new_rows, SUM(users) AS new_users_,
                   SUM(sessions) AS new_sessions
            FROM `{new}` WHERE date >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY) GROUP BY 1, 2
        ), o AS (
            SELECT date, dimension_type, COUNT(*) AS old_rows, SUM(users) AS old_users,
                   SUM(sessions) AS old_sessions
            FROM `{PROJECT}.{DATASET}.{TARGET}` WHERE date >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY) GROUP BY 1, 2
        )
        SELECT * FROM n FULL JOIN o USING (date, dimension_type) ORDER BY date, dimension_type
    """))
    print("\n  Device + Location totals in the new table vs raw Fivetran (should match exactly)")
    print_rows(run(client, f"""
        WITH n AS (
            SELECT date, dimension_type, SUM(users) AS new_users_ FROM `{new}`
            WHERE dimension_type IN ('Device', 'Location')
              AND date >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY) GROUP BY 1, 2
        ), r AS (
            SELECT date, 'Device' AS dimension_type, SUM(total_users) AS raw_users
            FROM `{PROJECT}.{DATASET}.tech_device_category_report` GROUP BY 1
            UNION ALL
            SELECT date, 'Location', SUM(total_users)
            FROM `{PROJECT}.{DATASET}.demographic_city_report` GROUP BY 1
        )
        SELECT dimension_type, COUNT(*) AS days, COUNTIF(new_users_ != raw_users) AS mismatched_days
        FROM n JOIN r USING (date, dimension_type) GROUP BY 1
    """))
    print("\n  Sample rows for the most recent day")
    print_rows(run(client, f"""
        SELECT * FROM `{new}`
        WHERE date = (SELECT MAX(date) FROM `{new}`)
          AND (dimension_type = 'Device' OR dimension_value = '(other)' OR dimension_value = '/')
        ORDER BY dimension_type, dimension_value
    """), width=30)

    section("5. GA4 Airtable table: last 7 days by dimension_type")
    pat = os.getenv("AIRTABLE_PAT")
    base_id = os.getenv("AIRTABLE_BASE_ID")
    table_id = os.getenv("AIRTABLE_TABLE_ID")
    if not (pat and base_id and table_id):
        print("  (skipped: AIRTABLE_PAT / AIRTABLE_BASE_ID / AIRTABLE_TABLE_ID not set)")
        return
    try:
        from pyairtable import Api

        records = Api(pat).table(base_id, table_id).all(
            formula="IS_AFTER({date}, DATEADD(TODAY(), -8, 'days'))"
        )
        totals = defaultdict(lambda: defaultdict(float))
        for rec in records:
            f = rec["fields"]
            key = (f.get("date"), f.get("dimension_type"))
            totals[key]["rows_"] += 1
            for m in ("users", "new_users", "sessions", "key_events"):
                totals[key][m] += f.get(m) or 0
        for (d, dt), m in sorted(totals.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
            print(f"  date={d} | dimension_type={dt} | " +
                  " | ".join(f"{k}={int(v)}" for k, v in m.items()))
        if not records:
            print("  (no records in the last 7 days)")
    except Exception as e:
        print(f"  AIRTABLE READ FAILED: {e}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Fatal error: {e}")
        sys.exit(1)
